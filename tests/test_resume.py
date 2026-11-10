"""plan → verify → render, on a fictional person. No real personal data belongs in this file."""
import base64
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from profile_atlas import render as R
from profile_atlas.config import Config, PrivacyError, ensure_private
from profile_atlas.kmap import KmapIndex
from profile_atlas.plan import build_plan, builtin_roles, score
from profile_atlas.profile import validate_profile
from profile_atlas.sync import sync
from profile_atlas.verify import verify

KMAP = KmapIndex.from_graph({
    "nodes": {"id": ["t1", "t2", "n1", "n2"], "slug": ["post-training", "recsys", "lora", "semantic-id"],
              "kind": ["topic", "topic", "method", "method"], "layer": ["knowledge"] * 4,
              "name": ["Post-training", "RecSys", "LoRA", "Semantic ID"], "name_zh": [None] * 4,
              "summary": [None] * 4, "topics": [[], [], [0], [1]]},
    "topics": [{"id": "t1", "slug": "post-training", "parents": []}, {"id": "t2", "slug": "recsys", "parents": []}],
}, source="test")

ROLE = {"id": "algo", "title": "算法工程师", "theme": "minimal",
        "weights": {"topics": {"post-training": 1.0}, "techs": {}}, "limits": {"projects": 2, "bullets": 3}}

PROFILE = {
    "schema": "portfolio/profile@1",
    "name": "李小雨",
    "public_name": "候选人 A",
    "contact": {"phone": "139-0000-1234", "email": "xiaoyu@example.com", "github": "github.com/xiaoyu-demo"},
    "education": [{"school": "示例理工大学", "degree": "硕士", "major": "人工智能", "start": "2024-09",
                   "details": ["GPA 3.9 / 4.0"]}],
    "experience": [{"id": "acme-2025", "org": "示例科技有限公司", "public_alias": "某科技公司", "title": "算法实习生",
                    "period": {"start": "2025-06", "end": "2025-09"},
                    "projects": [
                        {"id": "sft", "title": "领域 SFT", "kind": "internship", "techs": [{"slug": "lora", "depth": "core"}],
                         "facts": ["通过率 61.5% → 70.2%，评测集 1,200 条"],
                         "metrics": [{"name": "通过率", "value": 70.2, "baseline": {"value": 61.5}, "scale": "full"}],
                         "verified": True},
                        {"id": "draft", "title": "未核对的项目", "kind": "internship", "facts": ["提升 3 倍"],
                         "verified": False}]}],
    "projects": [{"id": "rec", "title": "生成式推荐", "kind": "competition", "techs": ["semantic-id"],
                  "facts": ["总分 0.81 → 0.86"], "verified": True}],
    "publications": [{"title": "A Paper That Names Its Author", "status": "已发表"}],
}

MANIFEST = {
    "schema": "portfolio/project@1", "id": "rag", "title": "RAG 实验", "kind": "personal", "role": "solo",
    "period": {"start": "2025-01", "end": "2025-02"}, "visibility": "hidden", "status": "done",
    "summary": "检索增强与 LoRA 微调实验", "problem": "多跳问答召回不全的问题。",
    "techs": [{"slug": "lora", "depth": "used"}],
    "contributions": [{"what": "实现混合检索", "evidence": ["e"]}],
    "metrics": [{"name": "准确率", "value": 0.9, "baseline": {"value": 0.8, "label": "前"}, "setting": "10 条",
                 "scale": "smoke", "evidence": ["e"]},
                {"name": "Recall@5", "value": 0.569, "setting": "51 条", "scale": "pilot", "evidence": ["e"]}],
    "evidence": [{"id": "e", "type": "file", "path": "README.md"}],
}


def make_plan(tmp_path, manifests=None):
    cfg = Config(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    mdir = cfg.user_dir("demo") / "manifests"
    mdir.mkdir(parents=True)
    entries = []
    for i, m in enumerate(manifests if manifests is not None else [MANIFEST]):
        f = f"m{i}.yaml"
        (mdir / f).write_text(yaml.safe_dump(m, allow_unicode=True))
        entries.append({"key": f"lab/{m['id']}", "file": f, "ok": True, "status": m.get("status", "active"),
                        "visibility": "public", "project_url": f"https://github.com/lab/{m['id']}"})
    index = {"login": "demo", "manifests": entries}
    return cfg, build_plan(cfg, index, PROFILE, ROLE, KMAP)


def draft(**over):
    d = {"schema": "portfolio/resume@1", "role": "algo",
         "experience": [{"ref": "exp:acme-2025", "projects": [
             {"ref": "exp:acme-2025/sft", "title": "领域 SFT", "bullets": ["在 1,200 条评测集上通过率 61.5% → 70.2%"]}]}],
         "projects": [{"ref": "gh:lab/rag", "title": "RAG 实验", "bullets": ["多跳 Recall@5 达到 0.569"]}]}
    d.update(over)
    return d


def codes(rep):
    return [i.code for i in rep.issues if i.level == "error"]


# ---------------------------------------------------------------- profile & plan

def test_fixture_profile_is_valid():
    assert validate_profile(PROFILE) == []


def test_builtin_roles_load():
    roles = builtin_roles()
    assert {"llm-algorithm", "agent-dev", "search-rec", "embodied"} <= set(roles)


def test_score_uses_topic_membership_and_top_three():
    assert score([("lora", "core")], [], ROLE, KMAP) == 1.0          # lora ∈ post-training
    assert score([("semantic-id", "core")], [], ROLE, KMAP) == 0.0   # recsys not weighted
    many = [(f"x{i}", "used") for i in range(10)]
    role = {"weights": {"techs": {f"x{i}": 1.0 for i in range(10)}}}
    assert score(many, [], role, None) == pytest.approx(1.8)          # only the top three count


def test_plan_excludes_unverified_and_non_resume_status(tmp_path):
    idea = {**MANIFEST, "id": "idea-proj", "status": "idea"}
    _, plan = make_plan(tmp_path, [MANIFEST, idea])
    refs = {p["ref"] for p in plan["projects"]}
    assert "gh:lab/rag" in refs and "gh:lab/idea-proj" not in refs
    exp_refs = {p["ref"] for p in plan["experience"][0]["projects"]}
    assert exp_refs == {"exp:acme-2025/sft"}
    reasons = {x["ref"]: x["reason"] for x in plan["excluded"]}
    assert "verified: false" in reasons["exp:acme-2025/draft"] and "idea" in reasons["gh:lab/idea-proj"]


# ---------------------------------------------------------------- verify

def test_valid_draft_passes(tmp_path):
    _, plan = make_plan(tmp_path)
    assert verify(plan, draft()).ok


def test_invented_number_is_rejected(tmp_path):
    _, plan = make_plan(tmp_path)
    d = draft(projects=[{"ref": "gh:lab/rag", "title": "RAG 实验", "bullets": ["召回率提升 35%"]}])
    assert codes(verify(plan, d)) == ["number-not-in-facts"]


def test_smoke_metric_cannot_be_sold_as_improvement(tmp_path):
    _, plan = make_plan(tmp_path)
    bad = draft(projects=[{"ref": "gh:lab/rag", "title": "RAG 实验", "bullets": ["准确率从 0.8 提升到 0.9"]}])
    assert "smoke-as-improvement" in codes(verify(plan, bad))
    ok = draft(projects=[{"ref": "gh:lab/rag", "title": "RAG 实验", "bullets": ["打通训练链路（10 条样本验证，准确率 0.9）"]}])
    assert verify(plan, ok).ok


def test_unverified_or_unknown_refs_are_rejected(tmp_path):
    _, plan = make_plan(tmp_path)
    d = draft(experience=[{"ref": "exp:acme-2025", "projects": [
        {"ref": "exp:acme-2025/draft", "title": "未核对的项目", "bullets": ["提升 3 倍"]}]}])
    assert "ref-unknown" in codes(verify(plan, d))


def test_education_numbers_are_checked(tmp_path):
    _, plan = make_plan(tmp_path)
    d = draft(education=[{"ref": "edu:0", "details": ["GPA 3.95 / 4.0"]}])
    assert codes(verify(plan, d)) == ["number-not-in-facts"]


# ---------------------------------------------------------------- render & privacy

SECRETS = ["李小雨", "139-0000-1234", "13900001234", "xiaoyu@example.com", "示例科技有限公司", "示例理工大学",
           "xiaoyu-demo", "A Paper That Names Its Author"]


def test_private_render_has_everything(tmp_path):
    _, plan = make_plan(tmp_path)
    html = R.render_html(plan, draft(), "minimal", public=False)
    for s in ["李小雨", "139-0000-1234", "示例科技有限公司", "示例理工大学", "RAG 实验"]:
        assert s in html


def test_public_render_is_anonymous(tmp_path):
    _, plan = make_plan(tmp_path)
    html = R.render_html(plan, draft(), "minimal", public=True)
    assert R.leaks(html, R.forbidden_strings(plan)) == []
    for s in SECRETS:
        assert s.lower() not in html.lower(), s
    assert "候选人 A" in html and "某科技公司" in html
    assert "RAG 实验" not in html          # visibility: hidden
    assert "github.com" not in html        # public_github is off


def test_public_build_aborts_and_removes_stale_output(tmp_path):
    cfg, plan = make_plan(tmp_path)
    good = R.render(cfg, plan, draft(), "minimal", public=True, pdf=False)
    assert Path(good["html"]).exists()
    leaky = draft(headline="示例理工大学 · 算法")
    with pytest.raises(R.RenderError):
        R.render(cfg, plan, leaky, "minimal", public=True, pdf=False)
    assert not Path(good["html"]).exists()   # the older public build was removed, not left behind


def test_forbidden_matching_is_case_insensitive(tmp_path):
    _, plan = make_plan(tmp_path)
    assert "xiaoyu-demo" in R.leaks("contact: XIAOYU-DEMO", R.forbidden_strings(plan))


def test_public_requires_company_alias(tmp_path):
    _, plan = make_plan(tmp_path)
    plan["experience"][0]["public_alias"] = None
    with pytest.raises(R.RenderError, match="public_alias"):
        R.render_html(plan, draft(), "minimal", public=True)


def test_private_data_refused_inside_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(PrivacyError):
        ensure_private(tmp_path / "profile.yaml", "个人资料")


def test_pdf_page_count():
    assert R.pdf_pages(b"<< /Type /Pages /Count 1 >> << /Type /Page >>") == 1


# ---------------------------------------------------------------- sync

def test_sync_finds_nested_manifests_and_skips_templates(tmp_path):
    files = {("me/app", ".portfolio/project.yaml"): yaml.safe_dump(MANIFEST, allow_unicode=True),
             ("me/sandbox-x", "exp1/.portfolio/project.yaml"): yaml.safe_dump({**MANIFEST, "id": "exp1"}, allow_unicode=True)}
    repos = [{"full_name": "me/app", "template": False}, {"full_name": "me/sandbox-x", "template": False},
             {"full_name": "me/project-template", "template": True}]

    def fake(args):
        if args[:2] == ["api", "user"]:
            return "me\n"
        if args[1] == "user/orgs":
            return ""
        if args[:2] == ["api", "--paginate"]:
            return "\n".join(json.dumps({"full": r["full_name"], "owner": "me", "name": r["full_name"].split("/")[1],
                                         "private": True, "visibility": "private", "archived": False, "fork": False,
                                         "stars": 0, "pushed_at": "2025", "branch": "main",
                                         "url": f"https://github.com/{r['full_name']}", "topics": [],
                                         "template": r["template"]}) for r in repos) if "user/repos" in args[2] else ""
        if "/git/trees/" in args[1]:
            full = args[1].split("/git/")[0][len("repos/"):]
            return "\n".join(p for (f, p) in files if f == full) + "\nREADME.md"
        if "/contents/" in args[1]:
            full, path = args[1][len("repos/"):].split("/contents/")
            return base64.b64encode(files[(full, path.split("?")[0])].encode()).decode()
        raise AssertionError(args)

    cfg = Config(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    idx = sync(cfg, run=fake, workers=1)
    assert [m["key"] for m in idx["manifests"]] == ["me/app", "me/sandbox-x/exp1"]
    assert all(m["ok"] for m in idx["manifests"])
    assert not (cfg.user_dir("me") / "manifests.partial").exists()


# ---------------------------------------------------------------- layout: photo, bold, fit

PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def test_photo_only_in_private_variant(tmp_path):
    _, plan = make_plan(tmp_path)
    photo = tmp_path / "me.png"
    photo.write_bytes(PNG_1PX)
    private = R.render_html(plan, draft(), "minimal", public=False, photo=photo)
    public = R.render_html(plan, draft(), "minimal", public=True, photo=photo)
    assert 'src="data:image/png;base64,' in private
    assert "data:image" not in public and "证件照" not in public


def test_photo_inside_git_repo_is_refused(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "me.jpg").write_bytes(b"x")
    _, plan = make_plan(tmp_path / "x")
    with pytest.raises(PrivacyError):
        R.render_html(plan, draft(), "minimal", public=False, photo=tmp_path / "me.jpg")


def test_bold_markup_is_rendered_and_everything_else_escaped(tmp_path):
    _, plan = make_plan(tmp_path)
    d = draft(experience=[{"ref": "exp:acme-2025", "projects": [
        {"ref": "exp:acme-2025/sft", "title": "领域 SFT",
         "bullets": ["**评测：**通过率 61.5% → **70.2%** <script>"]}]}])
    assert verify(plan, d).ok                      # ** doesn't disturb number checking
    html = R.render_html(plan, d, "minimal", public=False)
    assert "<strong>评测：</strong>" in html and "<strong>70.2%</strong>" in html
    assert "&lt;script&gt;" in html


def test_page_carries_fit_script_for_every_theme(tmp_path):
    _, plan = make_plan(tmp_path)
    for t in R.themes():
        html = R.render_html(plan, draft(), t, public=False)
        assert "root.dataset.fill" in html and "--fs" in html


def test_single_month_period_is_not_a_range():
    assert R._period({"start": "2026-03", "end": "2026-03"}) == "2026.03"
    assert R._period({"start": "2026-03", "end": None}) == "2026.03 - 至今"


# ---------------------------------------------------------------- duplicates & basis

def _with(profile_patch, manifests):
    import copy
    prof = copy.deepcopy(PROFILE)
    profile_patch(prof)
    return prof


def make_plan_with(tmp_path, prof, manifests):
    cfg = Config(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")
    mdir = cfg.user_dir("demo") / "manifests"
    mdir.mkdir(parents=True)
    entries = []
    for i, m in enumerate(manifests):
        f = f"m{i}.yaml"
        (mdir / f).write_text(yaml.safe_dump(m, allow_unicode=True))
        entries.append({"key": f"lab/{m['id']}", "file": f, "ok": True, "status": m.get("status", "active"),
                        "visibility": "public", "project_url": f"https://github.com/lab/{m['id']}"})
    return build_plan(cfg, {"login": "demo", "manifests": entries}, prof, ROLE, KMAP)


def test_local_project_linked_to_repo_appears_once_with_both_facts(tmp_path):
    prof = _with(lambda p: p["projects"][0].update(repo="lab/rag", facts=["定榜第 58 名"]), None)
    plan = make_plan_with(tmp_path, prof, [MANIFEST])
    refs = [p["ref"] for p in plan["projects"]]
    assert refs == ["gh:lab/rag"]
    assert any(x["ref"] == "local:rec" and "已并入" in x["reason"] for x in plan["excluded"])
    d = draft(projects=[{"ref": "gh:lab/rag", "title": "RAG 实验", "bullets": ["Recall@5 0.569，定榜第 58 名"]}])
    assert verify(plan, d).ok
    stale = draft(projects=[{"ref": "local:rec", "title": "生成式推荐", "bullets": ["总分 0.86"]}])
    rep = verify(plan, stale)
    assert "ref-unknown" in codes(rep) and "已并入" in rep.issues[0].message


def test_internship_linked_to_repo_stays_in_experience(tmp_path):
    prof = _with(lambda p: p["experience"][0]["projects"][0].update(repo="lab/rag"), None)
    plan = make_plan_with(tmp_path, prof, [MANIFEST])
    assert "gh:lab/rag" not in [p["ref"] for p in plan["projects"]]
    sft = plan["experience"][0]["projects"][0]
    assert sft["facts"]["repo"]["metrics"] and sft["visibility"] == "hidden"   # repo is hidden → stricter wins
    # the repo's smoke metric (0.8 → 0.9) can't be sold as an improvement under the internship either
    d = draft(experience=[{"ref": "exp:acme-2025", "projects": [
        {"ref": "exp:acme-2025/sft", "title": "领域 SFT", "bullets": ["准确率 0.8 → 0.9"]}]}], projects=[])
    assert "smoke-as-improvement" in codes(verify(plan, d))


def test_pending_source_with_verified_true_is_flagged():
    from profile_atlas.profile import consistency_warnings
    import copy
    prof = copy.deepcopy(PROFILE)
    prof["projects"][0].update(source="导入自旧简历，待本人确认", basis="attested")
    w = consistency_warnings(prof)
    assert any("rec" in x and "待确认" in x for x in w)
    prof["projects"][0]["source"] = "本人确认：原始记录留在原公司"
    assert not any(x.startswith("rec:") for x in consistency_warnings(prof))
    assert validate_profile(prof) == []


def test_merged_period_is_the_union(tmp_path):
    prof = _with(lambda p: p["projects"][0].update(repo="lab/rag", period={"start": "2024-12", "end": "2025-01"}), None)
    plan = make_plan_with(tmp_path, prof, [MANIFEST])
    assert plan["projects"][0]["period"] == {"start": "2024-12", "end": "2025-02"}
