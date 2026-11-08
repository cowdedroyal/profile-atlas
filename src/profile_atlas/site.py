"""A local page that lists every rendered resume variant (role × private/public × theme) side by side."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from .config import Config, ensure_private


def variants(root: Path) -> list[dict]:
    out = []
    for h in sorted(root.glob("out/*/*/resume.html")):
        role, variant = h.parts[-3], h.parts[-2]
        mode, _, theme = variant.partition("-")
        meta_f = h.parent / "meta.json"
        meta = json.loads(meta_f.read_text("utf-8")) if meta_f.exists() else {}
        pdf = h.parent / "resume.pdf"
        out.append({"role": role, "role_title": meta.get("role_title", role), "mode": mode, "theme": theme,
                    "html": h.relative_to(root).as_posix(),
                    "pdf": pdf.relative_to(root).as_posix() if pdf.exists() else None,
                    "pages": meta.get("pages"),
                    "updated": datetime.fromtimestamp(h.stat().st_mtime).strftime("%m-%d %H:%M")})
    return out


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>简历版本</title>
<style>
:root {{ --bg:#f6f5f2; --panel:#fff; --ink:#1a1a1a; --muted:#6b6b6b; --line:#dedbd5; --on:#2f5d8a; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#161616; --panel:#1f1f1f; --ink:#ececec; --muted:#9a9a9a; --line:#333; --on:#7fb0e0; }} }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 "PingFang SC",system-ui,sans-serif; }}
.wrap {{ display:grid; grid-template-columns: 280px 1fr; height:100vh; }}
aside {{ border-right:1px solid var(--line); padding:16px; overflow:auto; background:var(--panel); }}
h1 {{ font-size:15px; margin:0 0 4px; }} .note {{ color:var(--muted); font-size:12px; margin:0 0 14px; }}
h2 {{ font-size:12px; color:var(--muted); margin:16px 0 6px; letter-spacing:.05em; }}
button {{ display:block; width:100%; text-align:left; border:1px solid var(--line); background:none; color:inherit; border-radius:6px; padding:8px 10px; margin:0 0 6px; cursor:pointer; font:inherit; }}
button.on {{ border-color:var(--on); box-shadow: inset 3px 0 0 var(--on); }}
button small {{ display:block; color:var(--muted); font-size:12px; }}
.view {{ display:flex; flex-direction:column; min-width:0; }}
.bar {{ padding:10px 16px; border-bottom:1px solid var(--line); display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
.bar a {{ color:var(--on); }}
iframe {{ flex:1; border:0; width:100%; background:#eceae6; }}
@media (max-width: 760px) {{ .wrap {{ grid-template-columns:1fr; height:auto; }} aside {{ border-right:0; border-bottom:1px solid var(--line); }} iframe {{ height:80vh; }} }}
</style></head><body><div class="wrap">
<aside><h1>简历版本</h1><p class="note">只在本机。完整版含联系方式和公司名，不要上传。</p>{groups}</aside>
<div class="view"><div class="bar" id="bar"></div><iframe id="frame" title="简历预览"></iframe></div>
</div>
<script>
const V = {data};
const bar = document.getElementById('bar'), frame = document.getElementById('frame');
function show(i) {{
  const v = V[i];
  document.querySelectorAll('button[data-i]').forEach(b => b.classList.toggle('on', +b.dataset.i === i));
  frame.src = v.html;
  bar.innerHTML = `<strong>${{v.role_title}}</strong><span>${{v.mode === 'public' ? '公开版' : '完整版'}} · ${{v.theme}}</span>` +
    (v.pdf ? `<a href="${{v.pdf}}" target="_blank" rel="noopener">PDF${{v.pages ? '（' + v.pages + ' 页）' : ''}}</a>` : '') +
    `<a href="${{v.html}}" target="_blank" rel="noopener">单独打开</a>`;
  try {{ localStorage.setItem('pa-variant', i); }} catch (e) {{}}
}}
document.querySelectorAll('button[data-i]').forEach(b => b.addEventListener('click', () => show(+b.dataset.i)));
let start = 0; try {{ start = +(localStorage.getItem('pa-variant') || 0); }} catch (e) {{}}
if (V.length) show(Math.min(start, V.length - 1));
</script></body></html>
"""


def build_site(cfg: Config, login: str) -> Path:
    root = ensure_private(cfg.user_dir(login), "数据目录")
    vs = variants(root)
    groups, by_role = [], {}
    for i, v in enumerate(vs):
        by_role.setdefault((v["role"], v["role_title"]), []).append((i, v))
    for (role, title), items in by_role.items():
        groups.append(f"<h2>{html.escape(title)}</h2>")
        for i, v in items:
            label = ("公开版" if v["mode"] == "public" else "完整版") + f" · {v['theme']}"
            groups.append(f'<button data-i="{i}">{html.escape(label)}<small>{v["updated"]}'
                          f'{" · " + str(v["pages"]) + " 页" if v["pages"] else ""}</small></button>')
    if not vs:
        groups.append('<p class="note">还没有渲染任何简历：运行 profile-atlas render --role …</p>')
    page = PAGE.format(groups="\n".join(groups), data=json.dumps(vs, ensure_ascii=False))
    out = root / "index.html"
    out.write_text(page, encoding="utf-8")
    return out
