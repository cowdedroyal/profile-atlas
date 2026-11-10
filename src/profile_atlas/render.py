"""Render a verified draft to HTML (and PDF via headless Chrome), in a private or public variant.

private: everything, for your own machine and for sending as a PDF.
public:  anonymous — public_name instead of the real name; no phone, email, publications or (unless
         public_github) GitHub links; companies and schools only by their public_alias; hidden items
         dropped. The output is then scanned, case-insensitively, for the name, contact values,
         employers, schools, GitHub login and public_redact entries; any hit aborts the build.
         The ID photo is never part of the public variant.

Layout: one template, themes only change colours. A script in the page picks the largest font size that
still fits one A4 page; after printing, the chosen size and fill ratio are read back to warn about
pages that are clearly too empty or still overflow.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from .config import Config, ensure_private, secure_dir


class RenderError(Exception):
    pass


# Feather-style outline icons (MIT), drawn with the theme's --icon colour
ICONS = {
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "email": '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>',
    "github": '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>',
    "website": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "location": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    "target": '<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>',
}

_BOLD = re.compile(r"\*\*(.+?)\*\*")
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
FILL_LOW = 0.85  # below this the page looks unfinished even at the largest font size


def rich(text: str) -> Markup:
    """Escape, then turn **x** into <strong>x</strong> — the only markup a draft may use."""
    return Markup(_BOLD.sub(r"<strong>\1</strong>", str(escape(text))))


def photo_uri(path: str | Path | None) -> str | None:
    """The ID photo as a data: URI so the HTML stays a single file. Private data: refused inside git repos."""
    if not path:
        return None
    p = ensure_private(Path(os.path.expanduser(str(path))), "证件照")
    if not p.exists():
        raise RenderError(f"找不到证件照 {p}；检查个人资料里的 photo")
    mime = PHOTO_TYPES.get(p.suffix.lower())
    if not mime:
        raise RenderError(f"证件照只支持 {'/'.join(PHOTO_TYPES)}：{p.name}")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _period(p: dict | None) -> str:
    if not p:
        return ""
    fmt = lambda m: m.replace("-", ".") if m else "至今"  # noqa: E731
    if p.get("end") == p.get("start"):
        return fmt(p.get("start"))
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


def view_model(plan: dict, draft: dict, public: bool, photo: str | Path | None = None) -> dict:
    prof = plan["profile"]
    c = prof.get("contact") or {}
    contact = []
    links_ok = not public or bool(prof.get("public_github"))
    if not public:
        contact += [{"kind": k, "text": c[k]} for k in ("phone", "email") if c.get(k)]
    if c.get("github") and links_ok:
        gh = c["github"]
        contact.append({"kind": "github", "text": re.sub(r"^https?://", "", gh),
                        "href": gh if gh.startswith("http") else f"https://{gh}"})
    if not public:
        if c.get("website"):
            w = c["website"]
            contact.append({"kind": "website", "text": re.sub(r"^https?://", "", w).rstrip("/"),
                            "href": w if w.startswith("http") else f"https://{w}"})
        if c.get("location"):
            contact.append({"kind": "location", "text": c["location"]})

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
        # a face identifies the person as surely as a name: the public variant never carries one
        "photo": None if public else photo_uri(photo or prof.get("photo")),
        "headline": draft.get("headline") or prof.get("headline"),
        "summary": draft.get("summary"), "contact": contact, "education": education,
        "experience": experience, "projects": projects,
        # paper titles lead straight to the author's name, so the public variant never lists them
        "publications": prof.get("publications") if show.get("publications", True) and not public else [],
        "awards": prof.get("awards") if show.get("awards", True) else [],
        "skills": draft.get("skills") or prof.get("skills") or [],
    }


def render_html(plan: dict, draft: dict, theme: str, public: bool, photo: str | Path | None = None) -> str:
    if theme not in themes():
        raise RenderError(f"没有主题 {theme}；可选：{', '.join(themes())}")
    root = resources.files("profile_atlas")
    css = root.joinpath("themes/base.css").read_text("utf-8") + "\n" + root.joinpath(f"themes/{theme}.css").read_text("utf-8")
    with resources.as_file(root.joinpath("templates")) as tdir:
        env = Environment(loader=FileSystemLoader(str(tdir)), autoescape=select_autoescape(["html", "j2"]))
        env.filters["rich"] = rich
        return env.get_template("resume.html.j2").render(r=view_model(plan, draft, public, photo), css=css,
                                                         theme=theme, ICONS=ICONS)


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


def measure(chrome: str, html_path: Path) -> dict:
    """Run the page's fit script in headless Chrome and read back the chosen font size and fill ratio."""
    try:
        dom = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--window-size=1200,1700",
                              "--virtual-time-budget=3000", "--dump-dom", html_path.as_uri()],
                             check=True, capture_output=True, timeout=120, text=True).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    m = re.search(r"<html[^>]*>", dom)
    tag = m.group(0) if m else ""
    get = lambda k: (re.search(rf'data-{k}="([\d.]+)"', tag) or [None, None])[1]  # noqa: E731
    fs, fill = get("fs"), get("fill")
    return {"font_px": float(fs), "fill": float(fill)} if fs and fill else {}


def out_dir(cfg: Config, login: str, role_id: str, public: bool, theme: str) -> Path:
    return ensure_private(cfg.user_dir(login) / "out" / role_id / f"{'public' if public else 'private'}-{theme}", "输出目录")


def render(cfg: Config, plan: dict, draft: dict, theme: str | None, public: bool, pdf: bool = True,
           extra_forbidden: list[str] | None = None, photo: str | Path | None = None) -> dict:
    theme = theme or draft.get("theme") or plan["role"].get("theme") or "minimal"
    html = render_html(plan, draft, theme, public, photo)
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
            result.update(pdf=str(pp), pages=pages, **measure(chrome, hp))
            if pages > 1 or result.get("fill", 0) > 1:
                result["warning"] = f"PDF 有 {pages} 页，最小字号下仍放不下，需要删减内容"
            elif result.get("fill", 1) < FILL_LOW:
                result["warning"] = (f"最大字号下页面只填满 {result['fill']:.0%}，看起来偏空："
                                     "可补一条有证据的 bullet、加回一个项目，或展开 tagline")
    (d / "meta.json").write_text(json.dumps({"role_title": plan["role"].get("title"), "pages": result.get("pages"),
                                             "fill": result.get("fill"), "font_px": result.get("font_px"),
                                             "theme": theme, "mode": result["mode"]}, ensure_ascii=False), encoding="utf-8")
    return result
