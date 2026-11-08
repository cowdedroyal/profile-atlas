import subprocess
from pathlib import Path

import pytest
import yaml

from profile_atlas.check import check, number_in
from profile_atlas.kmap import KmapIndex

KMAP = KmapIndex.from_graph(
    {"nodes": {"slug": ["retrieval-augmented-generation", "lora"], "kind": ["method", "method"],
               "name": ["Retrieval-Augmented Generation", "LoRA"], "name_zh": ["检索增强生成", "低秩适配"]}},
    source="test",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    (r / "docs").mkdir()
    (r / "src" / "rag.py").write_text("def retrieve(q):\n    return fuse(dense, sparse)\n")
    (r / "docs" / "results.md").write_text("| hybrid | 0.5686 |\n| dense | 0.4412 |\n")
    (r / "README.md").write_text("QPS = 7.16\n")
    (r / ".gitignore").write_text("outputs/\n")
    (r / "outputs").mkdir()
    (r / "outputs" / "m.json").write_text('{"acc": 0.9}')
    _git(r, "init", "-q")
    _git(r, "add", ".")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return r


def manifest(**over) -> dict:
    base = {
        "schema": "portfolio/project@1",
        "id": "proj",
        "title": "测试项目",
        "kind": "personal",
        "role": "solo",
        "period": {"start": "2026-01", "end": None},
        "visibility": "public",
        "summary": "一个用于测试的项目",
        "problem": "这里说明根本问题是什么。",
        "techs": [{"slug": "retrieval-augmented-generation", "depth": "core"}],
        "contributions": [{"what": "实现融合检索", "evidence": ["code"]}],
        "metrics": [{"name": "Recall@5", "value": 0.569, "baseline": {"value": 0.441, "label": "dense"},
                     "setting": "51 条 QA", "scale": "pilot", "evidence": ["results"]}],
        "evidence": [
            {"id": "code", "type": "file", "path": "src/rag.py", "quote": ["return fuse(dense, sparse)"]},
            {"id": "results", "type": "file", "path": "docs/results.md"},
        ],
    }
    base.update(over)
    return base


def run(repo: Path, data: dict, kmap=KMAP):
    p = repo.parent / "project.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True))
    return check(repo, p, kmap)


def codes(report, level="error"):
    return [i.code for i in report.issues if i.level == level]


def test_valid_manifest_passes(repo):
    r = run(repo, manifest())
    assert r.ok, r.as_dict()
    assert codes(r, "warning") == []


def test_gitignored_evidence_is_rejected(repo):
    data = manifest()
    data["evidence"].append({"id": "m", "type": "file", "path": "outputs/m.json"})
    data["metrics"].append({"name": "acc", "value": 0.9, "setting": "in-domain", "scale": "smoke", "evidence": ["m"]})
    r = run(repo, data)
    assert "evidence-not-committed" in codes(r)
    assert "被 .gitignore" in next(i.message for i in r.issues if i.code == "evidence-not-committed")


def test_number_must_appear_in_evidence(repo):
    data = manifest()
    data["metrics"][0]["value"] = 0.61
    r = run(repo, data)
    assert codes(r) == ["number-not-in-evidence"]


def test_number_is_searched_only_in_quote_when_given(repo):
    data = manifest()
    data["evidence"][1]["quote"] = ["| dense | 0.4412 |"]  # quote does not contain 0.5686
    r = run(repo, data)
    assert codes(r) == ["number-not-in-evidence"]


def test_drifted_quote_is_reported(repo):
    data = manifest()
    data["evidence"][0]["quote"] = ["return fuse(sparse, dense)"]
    assert "quote-not-found" in codes(run(repo, data))


def test_quote_whitespace_is_normalised(repo):
    data = manifest()
    data["evidence"][0]["quote"] = ["def retrieve(q):   return fuse(dense, sparse)"]
    assert run(repo, data).ok


def test_unknown_evidence_ref(repo):
    data = manifest()
    data["contributions"][0]["evidence"] = ["nope"]
    assert "evidence-unknown" in codes(run(repo, data))


def test_unknown_tech_needs_proposed(repo):
    data = manifest()
    data["techs"].append({"slug": "reciprocal-rank-fusion", "depth": "core"})
    assert "tech-unknown" in codes(run(repo, data))
    data["techs"][-1]["proposed"] = {"name": "Reciprocal Rank Fusion", "kind": "method"}
    r = run(repo, data)
    assert r.ok
    assert [m["slug"] for m in r.kmap_missing] == ["reciprocal-rank-fusion"]


def test_readme_only_evidence_warns(repo):
    data = manifest()
    data["evidence"].append({"id": "readme", "type": "file", "path": "README.md"})
    data["metrics"].append({"name": "QPS", "value": 7.16, "setting": "A100", "scale": "pilot", "evidence": ["readme"]})
    r = run(repo, data)
    assert r.ok
    assert "self-claim-only" in codes(r, "warning")


def test_schema_limits(repo):
    r = run(repo, manifest(summary="长" * 61))
    assert codes(r) == ["schema"]


def test_contribution_tech_must_be_declared(repo):
    data = manifest()
    data["contributions"][0]["techs"] = ["lora"]
    assert "tech-not-declared" in codes(run(repo, data))


@pytest.mark.parametrize("text,value,ok", [
    ("0.5686", 0.569, True),
    ("0.5686", 0.57, True),
    ("0.57", 0.569, False),   # evidence less precise than the claim
    ("45.8%", 45.8, True),
    ("2.53 → 2.79", 2.79, True),
    ("0.0000", 0.0, True),
    ("120", 12, False),
])
def test_number_in(text, value, ok):
    assert number_in(text, value) is ok


def test_example_manifest_matches_schema():
    from jsonschema import Draft202012Validator
    from profile_atlas.check import load_schema

    root = Path(__file__).resolve().parents[1]
    for p in [root / "examples/langgraph-agentic-workflow/project.yaml", root / "src/profile_atlas/template/project.yaml"]:
        errors = list(Draft202012Validator(load_schema()).iter_errors(yaml.safe_load(p.read_text())))
        assert errors == [], (p, [e.message for e in errors])


def test_idea_and_archived_are_flagged_not_in_resume(repo):
    r = run(repo, manifest(status="archived"))
    assert r.ok and "not-in-resume" in codes(r, "info")
    assert "not-in-resume" not in codes(run(repo, manifest(status="done")), "info")


def test_number_in_ignores_hyphens_in_model_names():
    assert number_in("基于 Qwen3.5-9B 与 ZeRO-3", 9)
    assert number_in("Qwen3.6-35B-A3B", 35)
    assert not number_in("Qwen3.5-9B", 7)
