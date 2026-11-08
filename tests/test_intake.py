import json
import subprocess
from pathlib import Path

import pytest
import yaml

from profile_atlas import intake
from profile_atlas.check import check, validate_data
from profile_atlas.kmap import KmapIndex

KMAP = KmapIndex.from_graph(
    {"nodes": {"slug": ["lora", "instruction-tuning", "some-paper"], "kind": ["method", "method", "paper"],
               "layer": ["knowledge", "knowledge", "update"],
               "name": ["LoRA", "Instruction Tuning", "Some Paper"], "name_zh": ["低秩适配", "指令微调", None],
               "summary": ["只训练低秩增量矩阵的微调方法", "用指令数据微调", None]}},
    source="test",
)

SPEC = {
    "name": "lora-rank-ablation",
    "title": "LoRA 秩消融",
    "summary": "比较 LoRA 不同秩对指令微调的影响",
    "problem": "LoRA 的秩通常凭经验选，缺少系统对比。",
    "questions": ["秩从 8 到 64 收益是否饱和"],
    "techs": [{"slug": "instruction-tuning", "depth": "used"}, {"slug": "lora", "depth": "core"}],
}


def test_search_matches_chinese_and_skips_papers():
    assert [r["slug"] for r in KMAP.search("低秩")] == ["lora"]
    assert [r["slug"] for r in KMAP.search("用 LoRA 做微调")] == ["lora"]
    assert "some-paper" not in KMAP


def test_idea_manifest_needs_no_evidence():
    data = intake.manifest_from_spec(SPEC)
    assert data["status"] == "idea" and data["visibility"] == "hidden"
    assert validate_data(data, KMAP).ok


def test_active_manifest_needs_evidence():
    data = intake.manifest_from_spec(SPEC)
    data["status"] = "active"
    r = validate_data(data, KMAP)
    assert not r.ok and {i.code for i in r.issues} == {"schema"}
    del data["status"]  # default is active
    assert not validate_data(data, KMAP).ok


def test_topics_core_first_and_capped():
    data = intake.manifest_from_spec(SPEC)
    assert intake.topics_for(data) == ["lora", "instruction-tuning"]
    data["techs"] = [{"slug": f"t{i}", "depth": "used"} for i in range(30)]
    assert len(intake.topics_for(data)) == 20


def test_bad_spec_creates_nothing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(intake, "_run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 1, "", ""))
    spec = {**SPEC, "techs": [{"slug": "not-in-map", "depth": "core"}]}
    with pytest.raises(intake.IntakeError) as e:
        intake.plan(spec, KMAP, workspace=tmp_path)
    assert e.value.extra["report"]["errors"][0]["code"] == "tech-unknown"
    assert calls == []  # never reached gh


def test_check_in_sandbox_subdirectory(tmp_path):
    repo = tmp_path / "sandbox"
    proj = repo / "exp"
    (proj / "docs").mkdir(parents=True)
    (proj / "docs" / "r.md").write_text("acc 0.91\n")
    (repo / "other.md").write_text("acc 0.91\n")
    data = intake.manifest_from_spec({**SPEC, "name": "exp"})
    data.update(status="active", contributions=[{"what": "跑了消融实验", "evidence": ["r"]}],
                metrics=[{"name": "acc", "value": 0.91, "setting": "held-out", "scale": "pilot", "evidence": ["r"]}],
                evidence=[{"id": "r", "type": "file", "path": "docs/r.md"}])
    (proj / ".portfolio").mkdir()
    (proj / ".portfolio" / "project.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    r = check(proj, kmap=KMAP)
    assert r.ok, r.as_dict()
    data["evidence"][0]["path"] = "other.md"  # outside the project directory
    (proj / ".portfolio" / "project.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    assert "evidence-not-committed" in [i.code for i in check(proj, kmap=KMAP).issues]


# ---------------------------------------------------------------- end to end against a fake GitHub

class FakeGitHub:
    """Stands in for `gh` and github.com: repos are local bare repositories."""

    def __init__(self, root: Path):
        self.root = root
        self.repos: set[str] = set()
        self.topics: dict[str, list[str]] = {}
        root.mkdir(parents=True)
        seed = root / "_template"
        seed.mkdir()
        (seed / "README.md").write_text("# 项目名\n")
        (seed / ".portfolio").mkdir()
        (seed / ".portfolio" / "project.yaml").write_text("# template\n")
        self._git(seed, "init", "-q", "-b", "main")
        self._git(seed, "add", ".")
        self._git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "template")
        self.seed = seed

    @staticmethod
    def _git(cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def bare(self, full: str) -> Path:
        return self.root / f"{full}.git"

    def run(self, args, cwd=None, check=True):
        ok = lambda out="": subprocess.CompletedProcess(args, 0, out, "")  # noqa: E731
        if args[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(args, 0 if args[3] in self.repos else 1, "", "")
        if args[:3] == ["gh", "repo", "create"]:
            full = args[3]
            b = self.bare(full)
            b.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(b)], check=True)
            if "--template" in args:
                self._git(self.seed, "push", "-q", str(b), "main")
            self.repos.add(full)
            self.topics[full] = []
            return ok()
        if args[:3] == ["gh", "api", "user"]:
            return ok(json.dumps({"login": "tester", "id": 1}))
        if args[:2] == ["gh", "api"] and args[2].endswith("/topics"):
            return ok(json.dumps(self.topics[args[2][len("repos/"):-len("/topics")]]))
        if args[:2] == ["gh", "api"] and "/contents/" in args[2]:
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[:3] == ["gh", "repo", "edit"]:
            self.topics[args[3]] += args[args.index("--add-topic") + 1].split(",")
            return ok()
        if args[:2] == ["git", "clone"]:
            url, dest = args[-2], Path(args[-1])
            full = url.removeprefix("https://github.com/").removesuffix(".git")
            subprocess.run(["git", "clone", "-q", str(self.bare(full)), str(dest)], check=True, capture_output=True)
            self._git(dest, "remote", "set-url", "origin", url)
            self._git(dest, "config", f"url.{self.root.as_uri()}/.insteadOf", "https://github.com/")
            return ok()
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise intake.IntakeError(f"命令失败：{' '.join(args)}", stderr=p.stderr)
        return p


@pytest.fixture
def fake(monkeypatch, tmp_path):
    gh = FakeGitHub(tmp_path / "github")
    monkeypatch.setattr(intake, "_run", gh.run)
    return gh


def _log(repo_dir: Path) -> str:
    return subprocess.run(["git", "log", "--format=%an <%ae> %s"], cwd=repo_dir, capture_output=True, text=True).stdout


def test_new_repo_end_to_end(fake, tmp_path):
    ws = tmp_path / "lab"
    p = intake.plan(SPEC, KMAP, org="lab-org", workspace=ws)
    out = intake.execute(p)
    proj = ws / "lora-rank-ablation"
    assert out["repo"] == "lab-org/lora-rank-ablation"
    assert fake.topics["lab-org/lora-rank-ablation"] == ["lora", "instruction-tuning"]
    assert "tester <1+tester@users.noreply.github.com> Start project: LoRA 秩消融" in _log(proj)
    assert subprocess.run(["git", "status", "--porcelain", "-b"], cwd=proj, capture_output=True, text=True).stdout.strip() \
        == "## main...origin/main"  # pushed
    assert check(proj, kmap=KMAP).ok
    assert "## 想回答的问题" in (proj / "README.md").read_text()
    with pytest.raises(intake.IntakeError, match="已存在"):
        intake.plan(SPEC, KMAP, org="lab-org", workspace=ws)


def test_sandbox_end_to_end(fake, tmp_path):
    ws = tmp_path / "lab"
    intake.execute(intake.plan({**SPEC, "sandbox": "posttrain"}, KMAP, org="lab-org", workspace=ws))
    second = {**SPEC, "name": "sft-data-mix", "title": "SFT 数据配比", "sandbox": "posttrain",
              "techs": [{"slug": "instruction-tuning", "depth": "core"}]}
    intake.execute(intake.plan(second, KMAP, org="lab-org", workspace=ws))
    repo_dir = ws / "sandbox-posttrain"
    readme = (repo_dir / "README.md").read_text()
    assert "[lora-rank-ablation](lora-rank-ablation/)" in readme and "[sft-data-mix](sft-data-mix/)" in readme
    assert fake.topics["lab-org/sandbox-posttrain"] == ["lora", "instruction-tuning"]  # union, no duplicates
    for name in ("lora-rank-ablation", "sft-data-mix"):
        assert check(repo_dir / name, kmap=KMAP).ok


def test_new_repo_from_template(fake, tmp_path):
    fake.repos.add("lab-org/project-template")
    p = intake.plan(SPEC, KMAP, org="lab-org", workspace=tmp_path / "lab", template="lab-org/project-template")
    assert p["template"] == "lab-org/project-template"
    intake.execute(p)
    proj = tmp_path / "lab" / "lora-rank-ablation"
    log = subprocess.run(["git", "log", "--format=%s"], cwd=proj, capture_output=True, text=True).stdout
    assert "template" in log and "Start project" in log  # template commit, then ours
    assert check(proj, kmap=KMAP).ok


def test_missing_template_is_rejected_before_creating(fake, tmp_path):
    with pytest.raises(intake.IntakeError, match="模板仓库"):
        intake.plan(SPEC, KMAP, org="lab-org", workspace=tmp_path / "lab", template="lab-org/nope")
    assert "lab-org/lora-rank-ablation" not in fake.repos
