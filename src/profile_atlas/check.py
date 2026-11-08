"""Validate a project's .portfolio/project.yaml against its repository.

The rule this module enforces is the same one the knowledge map uses for edges: **no evidence, no
claim**. A contribution, decision, finding or metric reaches a resume only if it points at evidence
that a reader of the repository can open, and every number has to be findable in that evidence.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from .kmap import KmapIndex

MANIFEST = Path(".portfolio/project.yaml")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_CLAIM_SECTIONS = ("contributions", "decisions", "findings", "metrics")


def load_schema() -> dict:
    text = resources.files("profile_atlas").joinpath("schema/project.schema.json").read_text("utf-8")
    return json.loads(text)


@dataclass
class Issue:
    level: str  # error | warning | info
    where: str  # path inside the manifest, e.g. metrics[2].value
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "where": self.where, "code": self.code, "message": self.message}


@dataclass
class Report:
    manifest: str
    issues: list[Issue] = field(default_factory=list)
    kmap_missing: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, level: str, where: str, code: str, message: str) -> None:
        self.issues.append(Issue(level, where, code, message))

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def as_dict(self) -> dict:
        by = lambda lvl: [i.as_dict() for i in self.issues if i.level == lvl]  # noqa: E731
        return {
            "ok": self.ok,
            "manifest": self.manifest,
            "errors": by("error"),
            "warnings": by("warning"),
            "info": by("info"),
            "kmap_missing": self.kmap_missing,
            "stats": self.stats,
        }


# ---------------------------------------------------------------- repository access

class Repo:
    """The committed state of a project directory: what a reader on GitHub can actually see.

    ``root`` is the project directory. It is usually a repository's top level, but can also be a
    subdirectory of a sandbox repository; paths are always relative to ``root``.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        r = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True
        )
        self.is_git = r.returncode == 0 and r.stdout.strip() == "true"
        self._tracked: set[str] | None = None

    def tracked(self) -> set[str]:
        if self._tracked is None:
            if self.is_git:
                # Run inside root: git lists only files under it, relative to it.
                out = subprocess.run(
                    ["git", "-C", str(self.root), "-c", "core.quotepath=off", "ls-files", "-z"],
                    check=True, capture_output=True,
                ).stdout.decode("utf-8")
                self._tracked = {p for p in out.split("\0") if p}
            else:
                self._tracked = {
                    p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()
                }
        return self._tracked

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8", errors="replace")

    def has_commit(self, sha: str) -> bool:
        if not self.is_git:
            return False
        r = subprocess.run(
            ["git", "-C", str(self.root), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True
        )
        return r.returncode == 0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _decimals(x: float) -> int:
    s = repr(float(x))
    return 0 if s.endswith(".0") else len(s.split(".")[1]) if "." in s else 0


def number_in(text: str, value: float) -> bool:
    """True if ``text`` contains ``value``, allowing the text to be more precise (0.5686 ≈ 0.569).

    Signs are ignored: hyphens in names like "Qwen3.5-9B" or "ZeRO-3" would otherwise read as minus signs.
    """
    d = _decimals(value)
    for m in _NUM.finditer(text):
        tok = m.group().lstrip("-")
        tok_d = len(tok.split(".")[1]) if "." in tok else 0
        if tok_d < d:
            continue
        if round(float(tok), d) == round(abs(float(value)), d):
            return True
    return False


# ---------------------------------------------------------------- checks

def _schema_issues(data: object, report: Report) -> bool:
    """Add schema errors to ``report``; return True if the shape is valid."""
    errors = sorted(Draft202012Validator(load_schema()).iter_errors(data), key=lambda e: list(e.path))
    for e in errors:
        where = ".".join(str(p) if isinstance(p, str) else f"[{p}]" for p in e.path).replace(".[", "[")
        report.add("error", where, "schema", e.message)
    return not errors


def validate_data(data: dict, kmap: KmapIndex | None, where: str = "<spec>") -> Report:
    """Repository-independent checks (schema + technology slugs), used before a repo exists."""
    report = Report(manifest=where)
    if _schema_issues(data, report):
        _check_techs(data, kmap, report)
    return report


def check(repo_root: Path, manifest_path: Path | None = None, kmap: KmapIndex | None = None) -> Report:
    repo = Repo(repo_root)
    mpath = manifest_path or (repo.root / MANIFEST)
    report = Report(manifest=str(mpath))

    if not mpath.exists():
        report.add("error", "", "manifest-missing", f"找不到清单 {mpath}；用 `profile-atlas init` 生成骨架")
        return report
    try:
        data = yaml.safe_load(mpath.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        report.add("error", "", "yaml", f"YAML 解析失败：{e}")
        return report

    if not _schema_issues(data, report):
        return report  # later checks assume the shape is right

    if manifest_path is None and repo.is_git and MANIFEST.as_posix() not in repo.tracked():
        report.add("warning", "", "manifest-untracked", "清单还没有提交到仓库")

    evidence_text = _check_evidence(data, repo, report)
    used = _check_claims(data, evidence_text, report)
    for eid in evidence_text:
        if eid not in used:
            report.add("warning", f"evidence[{eid}]", "evidence-unused", "这条证据没有被任何内容引用")
    _check_techs(data, kmap, report)
    if data.get("status", "active") in ("idea", "archived"):
        report.add("info", "status", "not-in-resume", f"status: {data['status']}：这个项目不会进入任何简历")

    report.stats = {
        "status": data.get("status", "active"),
        "techs": len(data["techs"]),
        "contributions": len(data.get("contributions", [])),
        "decisions": len(data.get("decisions", [])),
        "findings": len(data.get("findings", [])),
        "metrics": len(data.get("metrics", [])),
        "evidence": len(data.get("evidence", [])),
        "metric_scales": _count(m["scale"] for m in data.get("metrics", [])),
    }
    return report


def _check_evidence(data: dict, repo: Repo, report: Report) -> dict[str, dict]:
    """Verify each evidence item; return id -> {"text": searchable text or None, "self_claim": bool}."""
    out: dict[str, dict] = {}
    tracked = repo.tracked()
    for i, ev in enumerate(data.get("evidence", [])):
        eid, where = ev["id"], f"evidence[{i}]"
        if eid in out:
            report.add("error", where, "evidence-dup", f"证据 id 重复：{eid}")
            continue
        text: str | None = None
        kind = ev["type"]
        if kind == "file":
            path = ev["path"]
            if path not in tracked:
                exists = (repo.root / path).exists()
                hint = "文件在本地但没有提交（多半被 .gitignore 掉了）" if exists else "仓库里没有这个文件"
                report.add("error", where, "evidence-not-committed", f"{path}：{hint}，读者看不到")
            else:
                full = repo.read(path)
                quotes = ev.get("quote") or []
                missing = [q for q in quotes if _norm(q) not in _norm(full)]
                for q in missing:
                    report.add("error", where, "quote-not-found", f"{path} 里找不到片段：{q[:60]}")
                text = "\n".join(quotes) if quotes and not missing else full
        elif kind == "commit":
            if not repo.has_commit(ev["sha"]):
                report.add("error", where, "commit-missing", f"仓库里没有提交 {ev['sha']}")
        elif kind == "external":
            report.add("warning", where, "evidence-external", f"仓库外证据，读者无法核实：{ev['note']}")
        out[eid] = {
            "text": text,
            "type": kind,
            "self_claim": kind == "file" and Path(ev["path"]).name.lower().startswith("readme"),
        }
    return out


def _check_claims(data: dict, evidence: dict[str, dict], report: Report) -> set[str]:
    used: set[str] = set()
    for section in _CLAIM_SECTIONS:
        for i, item in enumerate(data.get(section, [])):
            where = f"{section}[{i}]"
            refs = item["evidence"]
            for r in refs:
                if r not in evidence:
                    report.add("error", where, "evidence-unknown", f"引用了不存在的证据：{r}")
            known = [evidence[r] for r in refs if r in evidence]
            used.update(r for r in refs if r in evidence)
            if section == "metrics":
                _check_metric(item, known, where, report)
            elif known and all(e["self_claim"] for e in known):
                report.add("warning", where, "self-claim-only", "证据只有 README 自述，最好再指向代码、配置或结果文件")
    return used


def _check_metric(m: dict, known: list[dict], where: str, report: Report) -> None:
    texts = [e["text"] for e in known if e["text"] is not None]
    values = [("value", m["value"])]
    if "baseline" in m:
        values.append(("baseline.value", m["baseline"]["value"]))
    if texts:
        for key, v in values:
            if not any(number_in(t, v) for t in texts):
                report.add("error", f"{where}.{key}", "number-not-in-evidence", f"{m['name']} = {v} 在证据里找不到")
    if known and all(e["self_claim"] for e in known):
        report.add("warning", where, "self-claim-only",
                   f"{m['name']} 只有 README 自述作证，没有可复查的结果文件（如提交一份 metrics JSON 或 results.md）")
    if m["scale"] == "smoke":
        report.add("info", where, "scale-smoke", f"{m['name']} 是 smoke 级结果：简历上只能写成“验证链路打通”，不能当效果提升讲")


def _check_techs(data: dict, kmap: KmapIndex | None, report: Report) -> None:
    seen: set[str] = set()
    for i, t in enumerate(data["techs"]):
        where, slug = f"techs[{i}]", t["slug"]
        if slug in seen:
            report.add("error", where, "tech-dup", f"技术重复：{slug}")
        seen.add(slug)
        if kmap is None:
            continue
        if slug in kmap:
            if "proposed" in t:
                report.add("warning", where, "tech-proposed-exists", f"{slug} 已在知识地图里，去掉 proposed")
        elif "proposed" in t:
            report.kmap_missing.append({"slug": slug, **t["proposed"], "depth": t["depth"]})
        else:
            report.add("error", where, "tech-unknown",
                       f"知识地图里没有 {slug}：换成已有 slug，或者加 proposed 让它进入 kmap 扩展待办")
    if kmap is None:
        report.add("warning", "techs", "kmap-unavailable", "没有加载知识地图，跳过了技术 slug 校验")
    for i, c in enumerate(data.get("contributions", [])):
        for s in c.get("techs", []):
            if s not in seen:
                report.add("error", f"contributions[{i}]", "tech-not-declared", f"{s} 没有在 techs 里声明")


def _count(items) -> dict:
    out: dict = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out
