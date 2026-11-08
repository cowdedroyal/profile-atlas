"""Render a verified draft to HTML (and PDF via headless Chrome), in a private or public variant.

private: everything, for your own machine and for sending as a PDF.
public:  anonymous — public_name instead of the real name; no phone, email, publications or (unless
         public_github) GitHub links; companies and schools only by their public_alias; hidden items
         dropped. The output is then scanned, case-insensitively, for the name, contact values,
         employers, schools, GitHub login and public_redact entries; any hit aborts the build.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config, ensure_private, secure_dir


class RenderError(Exception):
    pass


def _period(p: dict | None) -> str:
    if not p:
        return ""
    fmt = lambda m: m.replace("-", ".") if m else "至今"  # noqa: E731
    return f"{fmt(p.get('start'))} - {fmt(p.get('end'))}"


def themes() -> list[str]:
    return sorted(f.name[:-4] for f in resources.files("profile_atlas").joinpath("themes").iterdir()
                  if f.name.endswith(".css") and f.name != "base.css")


def _mask(s: str) -> str:
    return s[0] + "*" * max(1, len(s) - 2) + s[-1] if len(s) > 2 else "*" * len(s)


def forbidden_strings(plan: dict, profile_extra: list[str] | None = None) -> list[str]:
    """Everything that identifies the person: name, phone, email, employers, schools, plus extras."""
    prof = plan["profile"]
    c = prof.get("contact") or {}
    out = [prof.get("name") or ""]
    out += [v for k, v in c.items() if k in ("phone", "email") and v]
    out += [re.sub(r"\D", "", c["phone"])] if c.get("phone") else []
    out += [c["email"].split("@")[0]] if c.get("email") else []
    out += [e["org"] for e in plan["experience"]]
    out += [e["school"] for e in prof.get("education") or []]
    if not prof.get("public_github"):
        gh = (c.get("github") or "").rstrip("/").split("/")[-1]
        out += [gh] if gh else []
    out += profile_extra or []
    return sorted({s for s in out if s and len(s) >= 2})


def leaks(html: str, forbidden: list[str]) -> list[str]:
    low = html.lower()
    return [s for s in forbidden if s.lower() in low]


def view_model(plan: dict, draft: dict, public: bool) -> dict:
    prof = plan["profile"]
    c = prof.get("contact") or {}
    contact = []
    links_ok = not public or bool(prof.get("public_github"))
    if not public:
        contact += [{"text": c[k]} for k in ("phone", "email") if c.get(k)]
    if c.get("github") and links_ok:
        gh = c["github"]
        contact.append({"text": gh, "href": gh if gh.startswith("http") else f"https://{gh}"})
    if c.get("website") and not public:
        contact.append({"text": c["website"], "href": c["website"]})

    edu_src = prof.get("education") or []
    edu_draft = draft.get("education") or [{"ref": f"edu:{i}", "details": e.get("details", [])} for i, e in enumerate(edu_src)]
    education = []
    for e in edu_draft:
        src = edu_src[int(e["ref"].split(":")[1])]
        if public and not src.get("public_alias"):
            continue  # no alias: the school would identify the person, so the entry is left out
        education.append({"school": src["public_alias"] if public else src["school"],
                          "major": src.get("major", ""), "degree": src["degree"],
                          "period": _period({"start": src["start"], "end": src.get("end")}),
                          "details": e.get("details", src.get("details", []))})

    by_ref = {p["ref"]: p for p in plan["projects"]}
    exps = {e["ref"]: e for e in plan["experience"]}

    def item(d: dict, pi: dict) -> dict | None:
        if public and pi.get("visibility") == "hidden":
            return None
        return {"title": d["title"], "tagline": d.get("tagline"), "bullets": d["bullets"],
                "period": _period(pi.get("period")), "url": pi.get("url") if links_ok else None}

    experience = []
    for x in draft.get("experience", []):
        pe = exps[x["ref"]]
        org = pe["org"]
        if public:
            if not pe.get("public_alias"):
                raise RenderError(f"{x['ref']} 没有 public_alias，公开版不能显示公司名；在个人资料里补上")
            org = pe["public_alias"]
        sub = {p["ref"]: p for p in pe["projects"]}
        items = [i for i in (item(p, sub[p["ref"]]) for p in x["projects"]) if i]
        experience.append({"org": org, "title": pe["title"], "period": _period(pe["period"]),
                           "summary": x.get("summary"), "projects": items})

    projects = [i for i in (item(p, by_ref[p["ref"]]) for p in draft.get("projects", [])) if i]
    show = draft.get("show") or {}
    return {
        "name": (prof.get("public_name") or "匿名") if public else prof["name"],
        "role_title": plan["role"].get("title", ""),
        "headline": draft.get("headline") or prof.get("headline"),
        "summary": draft.get("summary"), "contact": contact, "education": education,
        "experience": experience, "projects": projects,
        # paper titles lead straight to the author's name, so the public variant never lists them
        "publications": prof.get("publications") if show.get("publications", True) and not public else [],
        "awards": prof.get("awards") if show.get("awards", True) else [],
        "skills": draft.get("skills") or prof.get("skills") or [],
    }


def render_html(plan: dict, draft: dict, theme: str, public: bool) -> str:
    if theme not in themes():
        raise RenderError(f"没有主题 {theme}；可选：{', '.join(themes())}")
    root = resources.files("profile_atlas")
    css = root.joinpath("themes/base.css").read_text("utf-8") + "\n" + root.joinpath(f"themes/{theme}.css").read_text("utf-8")
    with resources.as_file(root.joinpath("templates")) as tdir:
        env = Environment(loader=FileSystemLoader(str(tdir)), autoescape=select_autoescape(["html", "j2"]))
        return env.get_template("resume.html.j2").render(r=view_model(plan, draft, public), css=css, theme=theme)


def find_chrome(cfg: Config) -> str | None:
    cands = [cfg.chrome, os.environ.get("PROFILE_ATLAS_CHROME"),
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Chromium.app/Contents/MacOS/Chromium",
             *sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/*.app/Contents/MacOS/*")), reverse=True),
             shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")]
    return next((c for c in cands if c and os.path.exists(c)), None)


def pdf_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![a-z])", pdf))


def to_pdf(chrome: str, html_path: Path, pdf_path: Path) -> int:
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--hide-scrollbars",
                    f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                   check=True, capture_output=True, timeout=120)
    return pdf_pages(pdf_path.read_bytes())


def out_dir(cfg: Config, login: str, role_id: str, public: bool, theme: str) -> Path:
    return ensure_private(cfg.user_dir(login) / "out" / role_id / f"{'public' if public else 'private'}-{theme}", "输出目录")


def render(cfg: Config, plan: dict, draft: dict, theme: str | None, public: bool, pdf: bool = True,
           extra_forbidden: list[str] | None = None) -> dict:
    theme = theme or draft.get("theme") or plan["role"].get("theme") or "minimal"
    html = render_html(plan, draft, theme, public)
    if public:
        hits = leaks(html, forbidden_strings(plan, extra_forbidden))
        if hits:
            # an older public build may still be on disk and is now known to be out of date: remove it
            stale = out_dir(cfg, plan["login"], plan["role"]["id"], True, theme)
            if stale.exists():
                shutil.rmtree(stale)
            masked = ", ".join(_mask(h) for h in hits)
            raise RenderError(f"公开版里出现了禁止的内容（{masked}），已中止；旧的公开版输出也已删除")
    d = out_dir(cfg, plan["login"], plan["role"]["id"], public, theme)
    secure_dir(cfg.user_dir(plan["login"]))
    d.mkdir(parents=True, exist_ok=True)
    hp = d / "resume.html"
    hp.write_text(html, encoding="utf-8")
    result = {"html": str(hp), "theme": theme, "mode": "public" if public else "private"}
    if pdf:
        chrome = find_chrome(cfg)
        if not chrome:
            result["pdf_error"] = "没有找到 Chrome/Chromium；在 config.yaml 里设置 chrome 路径，或只用 HTML"
        else:
            pp = d / "resume.pdf"
            pages = to_pdf(chrome, hp, pp)
            result.update(pdf=str(pp), pages=pages)
            if pages > 1:
                result["warning"] = f"PDF 有 {pages} 页，单页简历需要删减内容"
    (d / "meta.json").write_text(json.dumps({"role_title": plan["role"].get("title"), "pages": result.get("pages"),
                                             "theme": theme, "mode": result["mode"]}, ensure_ascii=False), encoding="utf-8")
    return result
