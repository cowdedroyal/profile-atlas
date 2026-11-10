"""profile-atlas command line. Every command prints JSON so agents can read the result."""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

import yaml

from .check import MANIFEST, check
from .config import PrivacyError, ensure_private, load_config
from .kmap import DEFAULT_SOURCE, KmapIndex


def _emit(obj: dict) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _load_kmap(source: str) -> KmapIndex | None:
    if source == "none":
        return None
    return KmapIndex.load(source)


# ---------------------------------------------------------------- project manifests

def cmd_check(args: argparse.Namespace) -> int:
    kmap = None
    kmap_error = None
    if args.kmap != "none":
        try:
            kmap = KmapIndex.load(args.kmap)
        except Exception as e:  # network or file problems should not hide the other checks
            kmap_error = f"{args.kmap}: {e}"
    report = check(Path(args.repo), Path(args.manifest) if args.manifest else None, kmap)
    out = report.as_dict()
    if kmap_error:
        out["warnings"].append({"level": "warning", "where": "techs", "code": "kmap-load-failed", "message": kmap_error})
    _emit(out)
    return 0 if report.ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    dest = repo / MANIFEST
    if dest.exists() and not args.force:
        _emit({"ok": False, "error": f"{dest} 已存在；用 --force 覆盖"})
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = resources.files("profile_atlas").joinpath("template/project.yaml")
    with resources.as_file(src) as p:
        shutil.copyfile(p, dest)
    _emit({"ok": True, "created": str(dest)})
    return 0


def cmd_techs(args: argparse.Namespace) -> int:
    kmap = _load_kmap(args.kmap)
    _emit({"ok": True, "source": kmap.source, "results": {q: kmap.search(q, args.limit) for q in args.query}})
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    from .intake import IntakeError, execute, plan

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        kmap = _load_kmap(args.kmap)
    except Exception as e:
        _emit({"ok": False, "error": f"加载知识地图失败：{e}；可以 --kmap none 跳过（技术 slug 将不校验）"})
        return 1
    try:
        p = plan(spec, kmap, org=args.org, workspace=Path(args.workspace).expanduser(),
                 template=None if args.template == "none" else args.template)
        if args.dry_run:
            _emit({"ok": True, "dry_run": True, **p})
            return 0
        _emit(execute(p))
        return 0
    except IntakeError as e:
        _emit({"ok": False, "error": str(e), **e.extra})
        return 1


def cmd_topics(args: argparse.Namespace) -> int:
    from .intake import IntakeError, sync_topics

    try:
        _emit({"ok": True, **sync_topics(Path(args.project), apply=args.apply)})
        return 0
    except IntakeError as e:
        _emit({"ok": False, "error": str(e), **e.extra})
        return 1


# ---------------------------------------------------------------- resumes

def cmd_sync(args: argparse.Namespace) -> int:
    from .sync import sync

    idx = sync(load_config())
    rows = [{"key": m["key"], "status": m.get("status"), "ok": m.get("ok"), "visibility": m.get("visibility")}
            for m in idx["manifests"]]
    _emit({"ok": True, "login": idx["login"], "repos_scanned": idx["repos_scanned"], "manifests": rows,
           "invalid": [m["key"] for m in idx["manifests"] if not m.get("ok")]})
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    from .profile import ProfileError, consistency_warnings, init_profile, load_profile, permission_warning

    cfg = load_config()
    try:
        if args.action == "init":
            p = init_profile(cfg, force=args.force)
            _emit({"ok": True, "created": str(p), "next": "编辑这个文件，然后运行 profile-atlas profile check"})
            return 0
        if args.action == "path":
            _emit({"ok": True, "path": str(cfg.profile_path)})
            return 0
        data = load_profile(cfg)
        unverified = [p["id"] for p in data.get("projects", []) if not p["verified"]]
        unverified += [f"{e['id']}/{p['id']}" for e in data.get("experience", []) for p in e.get("projects", [])
                       if not p["verified"]]
        warn = permission_warning(cfg.profile_path)
        _emit({"ok": True, "path": str(cfg.profile_path), "unverified": unverified,
               "warnings": ([warn] if warn else []) + consistency_warnings(data),
               "note": "unverified 里的项目在核对并改成 verified: true 之前不会进入简历" if unverified else None})
        return 0
    except (ProfileError, PrivacyError) as e:
        _emit({"ok": False, "error": str(e)})
        return 1


def cmd_roles(args: argparse.Namespace) -> int:
    from .plan import list_roles
    from .render import themes

    _emit({"ok": True, "roles": list_roles(load_config()), "themes": themes()})
    return 0


def _login(cfg, given: str | None) -> str:
    from .sync import load_index

    return given or load_index(cfg)["login"]


def draft_path(cfg, login: str, role_id: str) -> Path:
    return ensure_private(cfg.user_dir(login) / "drafts" / f"{role_id}.yaml", "简历草稿")


def cmd_plan(args: argparse.Namespace) -> int:
    from .plan import build_plan, load_role, save_plan
    from .profile import load_profile
    from .sync import load_index

    cfg = load_config()
    try:
        index = load_index(cfg, args.login)
        role = load_role(cfg, args.role)
        kmap = _load_kmap(args.kmap)
        plan = build_plan(cfg, index, load_profile(cfg), role, kmap)
        path = save_plan(cfg, plan)
    except Exception as e:  # noqa: BLE001 - surface any setup problem as JSON
        _emit({"ok": False, "error": str(e)})
        return 1
    dp = draft_path(cfg, plan["login"], role["id"])
    _emit({"ok": True, "plan": str(path), "draft": str(dp), "draft_exists": dp.exists(),
           "role": {"id": role["id"], "title": role.get("title"), "theme": role.get("theme")},
           "projects": [{"ref": p["ref"], "title": p["title"], "score": p["score"], "selected": p["selected"]}
                        for p in plan["projects"]],
           "experience": [{"ref": e["ref"], "projects": [{"ref": p["ref"], "score": p["score"]} for p in e["projects"]]}
                          for e in plan["experience"]],
           "excluded": plan["excluded"]})
    return 0


def _load_draft(cfg, login: str, role_id: str, path: str | None) -> dict:
    p = Path(path).expanduser() if path else draft_path(cfg, login, role_id)
    ensure_private(p, "简历草稿")
    if not p.exists():
        raise DraftError(f"找不到草稿 {p}：先运行 plan，再按 plan 输出的路径写草稿")
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        hint = "以 ** 开头的条目要加引号，例如 - '**数据构建：**…'" if "alias" in str(e) else ""
        raise DraftError(f"草稿不是合法的 YAML：{str(e).splitlines()[0]} {getattr(e, 'problem_mark', '') or ''} {hint}".strip())


class DraftError(Exception):
    pass


def cmd_verify(args: argparse.Namespace) -> int:
    from .plan import load_plan
    from .verify import verify

    cfg = load_config()
    login = _login(cfg, args.login)
    rep = verify(load_plan(cfg, login, args.role), _load_draft(cfg, login, args.role, args.draft))
    _emit(rep.as_dict())
    return 0 if rep.ok else 1


def cmd_render(args: argparse.Namespace) -> int:
    from .plan import load_plan
    from .profile import load_profile
    from .render import RenderError, render
    from .site import build_site
    from .verify import verify

    cfg = load_config()
    login = _login(cfg, args.login)
    plan = load_plan(cfg, login, args.role)
    draft = _load_draft(cfg, login, args.role, args.draft)
    rep = verify(plan, draft)
    if not rep.ok:
        _emit({"ok": False, "error": "草稿没有通过核对，先修正", **rep.as_dict()})
        return 1
    prof = load_profile(cfg)
    extra = prof.get("public_redact", [])
    try:
        # photo comes from the live profile, so adding one doesn't require re-planning
        outs = [render(cfg, plan, draft, args.theme, public=pub, pdf=not args.no_pdf, extra_forbidden=extra,
                       photo=prof.get("photo"))
                for pub in ([False, True] if args.both else [args.public])]
    except RenderError as e:
        _emit({"ok": False, "error": str(e)})
        return 1
    site = build_site(cfg, login)
    _emit({"ok": True, "outputs": outs, "warnings": rep.as_dict()["warnings"], "site": str(site)})
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    from .site import build_site

    cfg = load_config()
    login = _login(cfg, args.login)
    page = build_site(cfg, login)
    if not args.serve:
        _emit({"ok": True, "site": str(page)})
        return 0
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(page.parent))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as srv:
        print(json.dumps({"ok": True, "url": f"http://127.0.0.1:{srv.server_address[1]}/"}), flush=True)
        srv.serve_forever()
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Open the local overview page in the browser, or the output folder in the file manager.

    The data lives under ~/.local/share, which Finder hides; this saves looking for it.
    """
    import os
    import subprocess

    from .site import build_site

    cfg = load_config()
    login = _login(cfg, args.login)
    page = build_site(cfg, login)
    target = page.parent / "out" if args.folder else page
    target.mkdir(parents=True, exist_ok=True) if args.folder else None
    if sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    elif os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(target)], check=False)
    _emit({"ok": True, "opened": str(target)})
    return 0


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    from .intake import ORG, TEMPLATE, WORKSPACE

    ap = argparse.ArgumentParser(prog="profile-atlas", description="GitHub 项目清单 → 带证据的岗位简历")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="校验仓库里的 .portfolio/project.yaml")
    c.add_argument("repo", help="项目仓库目录")
    c.add_argument("--manifest", help="清单不在仓库里时指定路径（如 examples/ 下的样例）")
    c.add_argument("--kmap", default=DEFAULT_SOURCE, help="知识地图 graph JSON 的 URL 或文件；none 表示跳过")
    c.set_defaults(func=cmd_check)

    i = sub.add_parser("init", help="在仓库里生成清单骨架")
    i.add_argument("repo")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    t = sub.add_parser("techs", help="在知识地图里查技术，拿到 slug")
    t.add_argument("query", nargs="+", help="一个或多个查询词，中英文都行")
    t.add_argument("--limit", type=int, default=6)
    t.add_argument("--kmap", default=DEFAULT_SOURCE)
    t.set_defaults(func=cmd_techs)

    n = sub.add_parser("new", help="按 spec 立项：建私有仓库（或 sandbox 子目录）、写 idea 清单、推送、打 topic")
    n.add_argument("spec", help="spec JSON：name, title, summary, problem, questions?, techs, sandbox?, kind?, visibility?")
    n.add_argument("--org", default=ORG, help="建在哪个账号或组织下；默认 config.yaml 的 intake.org，否则当前登录账号")
    n.add_argument("--template", default=TEMPLATE or "none", help="模板仓库 owner/name；none 表示建空仓库")
    n.add_argument("--workspace", default=str(WORKSPACE), help="本地工作区（不要放在 iCloud 同步的目录里）")
    n.add_argument("--kmap", default=DEFAULT_SOURCE)
    n.add_argument("--dry-run", action="store_true", help="只校验并打印计划，不创建任何东西")
    n.set_defaults(func=cmd_new)

    tp = sub.add_parser("topics", help="把清单里的技术同步成 GitHub topic（只增不删）")
    tp.add_argument("project", help="项目目录（仓库根目录或 sandbox 子目录）")
    tp.add_argument("--apply", action="store_true", help="真的写到 GitHub；不加只预览")
    tp.set_defaults(func=cmd_topics)

    s = sub.add_parser("sync", help="用 gh 登录的账号，拉取本人和所属组织所有仓库里的项目清单")
    s.set_defaults(func=cmd_sync)

    pf = sub.add_parser("profile", help="本机私有资料：init 生成骨架，check 校验，path 显示位置")
    pf.add_argument("action", choices=["init", "check", "path"])
    pf.add_argument("--force", action="store_true")
    pf.set_defaults(func=cmd_profile)

    r = sub.add_parser("roles", help="列出可用岗位和主题")
    r.set_defaults(func=cmd_roles)

    pl = sub.add_parser("plan", help="按岗位选项目、排序，生成写作计划")
    pl.add_argument("--role", required=True, help="岗位 id，或岗位 yaml 文件路径")
    pl.add_argument("--login")
    pl.add_argument("--kmap", default=DEFAULT_SOURCE)
    pl.set_defaults(func=cmd_plan)

    v = sub.add_parser("verify", help="核对 agent 写的简历草稿：引用、数字、smoke 结果")
    v.add_argument("--role", required=True)
    v.add_argument("--draft", help="默认 ~/.local/share/profile-atlas/<login>/drafts/<role>.yaml")
    v.add_argument("--login")
    v.set_defaults(func=cmd_verify)

    rd = sub.add_parser("render", help="核对通过后渲染 HTML 和 PDF")
    rd.add_argument("--role", required=True)
    rd.add_argument("--theme")
    rd.add_argument("--public", action="store_true", help="公开版：去掉联系方式、公司用别称、不含 hidden 项目")
    rd.add_argument("--both", action="store_true", help="完整版和公开版都渲染")
    rd.add_argument("--no-pdf", action="store_true")
    rd.add_argument("--draft")
    rd.add_argument("--login")
    rd.set_defaults(func=cmd_render)

    st = sub.add_parser("site", help="生成本机的简历版本总览页；--serve 在 127.0.0.1 上打开")
    st.add_argument("--serve", action="store_true")
    st.add_argument("--port", type=int, default=0)
    st.add_argument("--login")
    st.set_defaults(func=cmd_site)

    op = sub.add_parser("open", help="在浏览器打开本机简历总览页；--folder 在访达/文件管理器里打开输出目录")
    op.add_argument("--folder", action="store_true")
    op.add_argument("--login")
    op.set_defaults(func=cmd_open)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except DraftError as e:
        _emit({"ok": False, "error": str(e)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
