"""Turn an idea into a project on GitHub: a private repo (or a sandbox subdirectory) with an
idea-stage manifest, pushed, and tagged with knowledge-map topics.

Everything that can be checked is checked before anything is created, so a bad spec never leaves
a half-made repository behind.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path

import yaml

from .check import MANIFEST, Report, validate_data
from .config import load_config
from .kmap import KmapIndex

_CFG = load_config()
ORG = _CFG.intake_org or ""          # empty: the logged-in account (resolved at run time)
WORKSPACE = _CFG.workspace
TEMPLATE = _CFG.template              # None: create a plain repo and write the skeleton ourselves

TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
MAX_TOPICS = 20
_DEPTH_ORDER = {"core": 0, "used": 1, "touched": 2}

MANIFEST_HEADER = (
    "# 这个项目的证据化清单。立项时 status: idea，可以还没有 contributions / evidence；\n"
    "# 做出东西后补上并改成 active。校验：profile-atlas check .\n"
)


class IntakeError(Exception):
    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.extra = extra


# ---------------------------------------------------------------- shell helpers

_TRANSIENT = ("EOF", "connection reset", "TLS handshake timeout", "i/o timeout", "502 Bad Gateway")


def _run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    # gh talks to the API over a flaky link here; retry only network-level failures.
    for attempt in range(3):
        if args[0] != "gh" or p.returncode == 0 or not any(t in p.stderr for t in _TRANSIENT):
            break
        time.sleep(2 * (attempt + 1))
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise IntakeError(f"命令失败：{' '.join(args)}", stderr=p.stderr.strip()[-800:])
    return p


def _gh_repo_exists(full: str) -> bool:
    return _run(["gh", "repo", "view", full, "--json", "name"], check=False).returncode == 0


def git_identity() -> tuple[str, str]:
    """GitHub login + noreply address: commits link to the account without exposing a real email."""
    info = json.loads(_run(["gh", "api", "user", "--jq", "{login: .login, id: .id}"]).stdout)
    return info["login"], f"{info['id']}+{info['login']}@users.noreply.github.com"


def _set_identity(repo_dir: Path, ident: tuple[str, str]) -> None:
    _run(["git", "config", "user.name", ident[0]], cwd=repo_dir)
    _run(["git", "config", "user.email", ident[1]], cwd=repo_dir)


def _clone(full: str, dest: Path, expect_commits: bool, tries: int = 10) -> None:
    """Clone; repos generated from a template are filled asynchronously, so wait for the first commit."""
    for attempt in range(tries):
        if dest.exists():
            _run(["rm", "-rf", str(dest)])
        _run(["git", "clone", "-q", f"https://github.com/{full}.git", str(dest)])
        if not expect_commits or _run(["git", "rev-parse", "HEAD"], cwd=dest, check=False).returncode == 0:
            return
        time.sleep(2 + attempt)
    raise IntakeError(f"{full} 克隆下来一直是空的（模板还没生成完？）稍后重试 git clone")


def github_repo_of(path: Path) -> str:
    # The raw config value: `git remote get-url` applies url.*.insteadOf rewrites.
    url = _run(["git", "config", "--get", "remote.origin.url"], cwd=path).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise IntakeError(f"origin 不是 GitHub 仓库：{url}")
    return m.group(1)


# ---------------------------------------------------------------- manifest

def manifest_from_spec(spec: dict, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    data = {
        "schema": "portfolio/project@1",
        "id": spec["name"],
        "title": spec["title"],
        **({"title_en": spec["title_en"]} if spec.get("title_en") else {}),
        "kind": spec.get("kind", "personal"),
        "role": spec.get("role", "solo"),
        "period": {"start": spec.get("start", today.strftime("%Y-%m")), "end": None},
        "visibility": spec.get("visibility", "hidden"),
        "status": "idea",
        "summary": spec["summary"],
        "problem": spec["problem"],
        "questions": spec.get("questions", []),
        "techs": spec["techs"],
        "contributions": [],
        "evidence": [],
    }
    if not data["questions"]:
        del data["questions"]
    return data


def dump_manifest(data: dict) -> str:
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    return MANIFEST_HEADER + body


def topics_for(data: dict) -> list[str]:
    """Repository topics = knowledge-map slugs, core technologies first."""
    out: list[str] = []
    for t in sorted(data["techs"], key=lambda t: _DEPTH_ORDER[t["depth"]]):
        s = t["slug"]
        if TOPIC_RE.match(s) and s not in out:
            out.append(s)
    return out[:MAX_TOPICS]


def _readme(data: dict) -> str:
    qs = "".join(f"- {q}\n" for q in data.get("questions", []))
    return (
        f"# {data['title']}\n\n> {data['summary']}\n\n## 问题\n\n{data['problem']}\n\n"
        + (f"## 想回答的问题\n\n{qs}\n" if qs else "")
        + "## 方案\n\n（做的时候补：核心思路和关键取舍）\n\n"
        "## 结果\n\n| 指标 | 基线 | 本项目 | 设置 | 规模 |\n|---|---|---|---|---|\n|  |  |  |  | smoke / pilot / full |\n\n"
        "结果文件放在 [docs/metrics/](docs/metrics/) 并提交。\n\n"
        "## 证据清单\n\n[.portfolio/project.yaml](.portfolio/project.yaml)：技术、做的事、取舍、发现和指标，每项都指向仓库里的证据。\n"
    )


# ---------------------------------------------------------------- plan / execute

def plan(spec: dict, kmap: KmapIndex | None, org: str = ORG, workspace: Path = WORKSPACE,
         template: str | None = TEMPLATE) -> dict:
    data = manifest_from_spec(spec)
    report: Report = validate_data(data, kmap)
    if not report.ok:
        raise IntakeError("清单不合格，没有创建任何东西", report=report.as_dict())
    org = org or json.loads(_run(["gh", "api", "user", "--jq", "{login: .login}"]).stdout)["login"]
    if template and not _gh_repo_exists(template):
        raise IntakeError(f"模板仓库 {template} 不存在；改 config.yaml 的 intake.template，或删掉它直接建空仓库")

    sandbox = spec.get("sandbox")
    if sandbox:
        if not re.match(r"^[a-z0-9][a-z0-9-]{1,40}$", sandbox):
            raise IntakeError(f"sandbox 名不合法：{sandbox}")
        repo = f"{org}/sandbox-{sandbox}"
        repo_dir = workspace / f"sandbox-{sandbox}"
        project_dir = repo_dir / spec["name"]
        create_repo = not _gh_repo_exists(repo)
        if not create_repo:
            p = _run(["gh", "api", f"repos/{repo}/contents/{spec['name']}"], check=False)
            if p.returncode == 0:
                raise IntakeError(f"{repo} 里已经有 {spec['name']}/ 了")
    else:
        repo = f"{org}/{spec['name']}"
        repo_dir = project_dir = workspace / spec["name"]
        if _gh_repo_exists(repo):
            raise IntakeError(f"{repo} 已存在；换个名字，或用 record 模式更新它")
        if repo_dir.exists():
            raise IntakeError(f"本地目录已存在：{repo_dir}")
        create_repo = True

    return {
        "repo": repo,
        "url": f"https://github.com/{repo}",
        "mode": "sandbox" if sandbox else "repo",
        "create_repo": create_repo,
        "local_repo": str(repo_dir),
        "project_dir": str(project_dir),
        "topics": topics_for(data),
        "kmap_missing": report.kmap_missing,
        "warnings": [i.as_dict() for i in report.issues if i.level == "warning"],
        "manifest": data,
        "template": template if not sandbox else None,
    }


def execute(p: dict) -> dict:
    data, repo = p["manifest"], p["repo"]
    repo_dir, project_dir = Path(p["local_repo"]), Path(p["project_dir"])
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    ident = git_identity()

    if p["mode"] == "repo":
        template = p.get("template")
        create = ["gh", "repo", "create", repo, "--private", "--description", data["summary"]]
        _run(create + (["--template", template] if template else []))
        _clone(repo, repo_dir, expect_commits=bool(template))
        if not template:
            _write_skeleton(project_dir)
    else:
        if p["create_repo"]:
            area = repo.split("/sandbox-", 1)[1]
            _run(["gh", "repo", "create", repo, "--private",
                  "--description", f"小探索合集（{area}）：成熟后毕业成独立仓库"])
        if repo_dir.exists():
            _run(["git", "pull", "-q", "--ff-only"], cwd=repo_dir)
        else:
            _clone(repo, repo_dir, expect_commits=False)
        _ensure_sandbox_readme(repo_dir, repo)
        (project_dir / "docs" / "metrics").mkdir(parents=True, exist_ok=True)
        (project_dir / "docs" / "metrics" / ".gitkeep").write_text("")
        _append_sandbox_entry(repo_dir, data)

    _set_identity(repo_dir, ident)
    (project_dir / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (project_dir / MANIFEST).write_text(dump_manifest(data), encoding="utf-8")
    (project_dir / "README.md").write_text(_readme(data), encoding="utf-8")

    _run(["git", "add", "-A"], cwd=repo_dir)
    _run(["git", "commit", "-qm", f"Start project: {data['title']}"], cwd=repo_dir)
    branch = _run(["git", "branch", "--show-current"], cwd=repo_dir).stdout.strip() or "main"
    _run(["git", "push", "-q", "-u", "origin", branch], cwd=repo_dir)

    topics = sync_topics(project_dir, apply=True)
    return {"ok": True, **{k: p[k] for k in ("repo", "url", "mode", "local_repo", "project_dir", "kmap_missing")},
            "topics": topics}


METRICS_README = (
    "# 结果文件\n\n要写进 README 或简历的数字，对应的结果文件（metrics JSON、评测表、日志摘要）放在这里并**提交**。\n"
    "`outputs/` 之类的运行目录会被 .gitignore 掉，读者看不到，profile-atlas 也不认。\n"
)


def _write_skeleton(project_dir: Path) -> None:
    (project_dir / "docs" / "metrics").mkdir(parents=True, exist_ok=True)
    (project_dir / "docs" / "metrics" / "README.md").write_text(METRICS_README, encoding="utf-8")
    gi = project_dir / ".gitignore"
    if not gi.exists():
        gi.write_text("__pycache__/\n*.py[cod]\n.venv/\n.env\n.DS_Store\noutputs/\n*.log\n", encoding="utf-8")


def _ensure_sandbox_readme(repo_dir: Path, repo: str) -> None:
    readme = repo_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {repo.split('/', 1)[1]}\n\n一个领域里的小探索。每个子目录是一个项目，带自己的 `.portfolio/project.yaml`。\n"
            "长成完整项目（有明确的问题、实验和结论）后毕业成独立仓库。\n\n## 项目\n\n",
            encoding="utf-8",
        )


def _append_sandbox_entry(repo_dir: Path, data: dict) -> None:
    readme = repo_dir / "README.md"
    line = f"- [{data['id']}]({data['id']}/)：{data['title']}，{data['summary']}\n"
    text = readme.read_text(encoding="utf-8")
    if line not in text:
        readme.write_text(text.rstrip("\n") + "\n" + line, encoding="utf-8")


def sync_topics(project_dir: Path, apply: bool) -> dict:
    """Add the manifest's technologies as GitHub topics. Add-only: topics set by hand are kept."""
    data = yaml.safe_load((project_dir / MANIFEST).read_text(encoding="utf-8"))
    repo = github_repo_of(project_dir)
    desired = topics_for(data)
    current = json.loads(_run(["gh", "api", f"repos/{repo}/topics", "--jq", ".names"]).stdout or "[]")
    to_add = [t for t in desired if t not in current]
    room = MAX_TOPICS - len(current)
    skipped = to_add[max(room, 0):]
    to_add = to_add[: max(room, 0)]
    if apply and to_add:
        _run(["gh", "repo", "edit", repo, "--add-topic", ",".join(to_add)])
    return {
        "repo": repo,
        "desired": desired,
        "added": to_add if apply else [],
        "would_add": [] if apply else to_add,
        "skipped_limit": skipped,
        "unmanaged": [t for t in current if t not in desired],
    }
