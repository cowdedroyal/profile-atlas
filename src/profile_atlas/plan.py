"""Turn synced manifests + the private profile into a role-specific plan: which items, in what order,
with which facts. The plan is what the writing agent works from, and what `verify` checks against.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import yaml

from .config import Config, ensure_private
from .kmap import KmapIndex
from .sync import load_manifest

DEPTH_W = {"core": 1.0, "used": 0.6, "touched": 0.3}
IN_RESUME = ("active", "done")


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- roles

def builtin_roles() -> dict[str, dict]:
    out = {}
    for f in resources.files("profile_atlas").joinpath("roles").iterdir():
        if f.name.endswith(".yaml"):
            r = yaml.safe_load(f.read_text("utf-8"))
            out[r["id"]] = r
    return out


def load_role(cfg: Config, name: str) -> dict:
    """A role by id (your ~/.config/profile-atlas/roles/ first, then built-ins) or a path to a role file."""
    p = Path(name).expanduser()
    if p.suffix in (".yaml", ".yml") and p.exists():
        return yaml.safe_load(p.read_text("utf-8"))
    mine = cfg.roles_dir / f"{name}.yaml"
    if mine.exists():
        return yaml.safe_load(mine.read_text("utf-8"))
    roles = builtin_roles()
    if name not in roles:
        raise PlanError(f"没有岗位 {name}；可选：{', '.join(sorted(roles))}，或在 {cfg.roles_dir} 下新建")
    return roles[name]


def list_roles(cfg: Config) -> list[dict]:
    roles = builtin_roles()
    if cfg.roles_dir.exists():
        for f in cfg.roles_dir.glob("*.yaml"):
            r = yaml.safe_load(f.read_text("utf-8"))
            roles[r["id"]] = {**r, "custom": True}
    return [{"id": r["id"], "title": r.get("title"), "description": r.get("description"), "custom": r.get("custom", False)}
            for r in sorted(roles.values(), key=lambda r: r["id"])]


# ---------------------------------------------------------------- scoring

def tech_weight(role: dict, slug: str, kmap: KmapIndex | None) -> float:
    w = role.get("weights", {})
    techs, topics = w.get("techs", {}) or {}, w.get("topics", {}) or {}
    best = max(float(techs.get(slug, 0)), float(topics.get(slug, 0)))
    node = kmap.get(slug) if kmap else None
    if node:
        for t in node.topics:
            best = max(best, float(topics.get(t, 0)))
    return best


def score(techs: list[tuple[str, str]], metrics: list[dict], role: dict, kmap: KmapIndex | None) -> float:
    """Relevance = the three most relevant technologies (depth × role weight). Counting only the top
    three keeps a project with many loosely related techs from beating one built around the role's core.
    Solid (non-smoke) metrics add a small tie-breaker."""
    parts = sorted((DEPTH_W.get(d, 0.6) * tech_weight(role, s, kmap) for s, d in techs), reverse=True)
    solid = [m for m in metrics if m.get("scale") != "smoke"]
    return round(sum(parts[:3]) + 0.05 * min(3, len(solid)), 3) if parts and parts[0] > 0 else 0.0


def _techs(raw: list) -> list[tuple[str, str]]:
    return [(t, "used") if isinstance(t, str) else (t["slug"], t.get("depth", "used")) for t in raw or []]


# ---------------------------------------------------------------- items

def github_item(entry: dict, m: dict, role: dict, kmap) -> dict:
    techs = _techs(m.get("techs"))
    metrics = m.get("metrics", [])
    public_repo = entry.get("visibility") == "public"
    return {
        "ref": f"gh:{entry['key']}",
        "source": "github",
        "title": m["title"],
        "kind": m.get("kind"),
        "period": m.get("period"),
        "status": m.get("status", "active"),
        "visibility": m.get("visibility", "hidden"),
        "url": entry["project_url"] if public_repo else None,
        "score": score(techs, metrics, role, kmap),
        "techs": [s for s, _ in techs],
        "facts": {
            "summary": m.get("summary"),
            "problem": m.get("problem"),
            "role": m.get("role"),
            "contributions": [{k: c[k] for k in ("what", "why") if k in c} for c in m.get("contributions", [])],
            "decisions": [{k: d[k] for k in ("choice", "why", "alternatives") if k in d} for d in m.get("decisions", [])],
            "findings": [{"claim": f["claim"], "kind": f["kind"]} for f in m.get("findings", [])],
            "metrics": metrics,
        },
    }


def local_item(p: dict, ref: str, role: dict, kmap) -> dict:
    techs = _techs(p.get("techs"))
    metrics = p.get("metrics", [])
    return {
        "ref": ref,
        "source": "experience" if ref.startswith("exp:") else "local",
        "title": p["title"],
        "kind": p["kind"],
        "period": p.get("period"),
        "visibility": "public" if p.get("public", True) else "hidden",
        "url": (p.get("links") or {}).get("repo"),
        "score": score(techs, metrics, role, kmap),
        "techs": [s for s, _ in techs],
        "facts": {"role": p.get("role"), "tagline": p.get("tagline"), "facts": p["facts"], "metrics": metrics,
                  "source": p.get("source"), "basis": p.get("basis")},
    }


def _absorb(keep: dict, other: dict, key: str) -> None:
    """Merge a duplicate's facts into the item that stays. The stricter visibility wins."""
    keep["facts"][key] = other["facts"]
    keep["techs"] = list(dict.fromkeys(keep["techs"] + other["techs"]))
    keep["score"] = max(keep["score"], other["score"])
    keep["url"] = keep.get("url") or other.get("url")
    pa, pb = keep.get("period"), other.get("period")
    if pa and pb:  # the union of both spans; an open end ("至今") wins
        ends = [pa.get("end"), pb.get("end")]
        keep["period"] = {"start": min(pa["start"], pb["start"]), "end": None if None in ends else max(ends)}
    else:
        keep["period"] = pa or pb
    if "hidden" in (keep.get("visibility"), other.get("visibility")):
        keep["visibility"] = "hidden"
    keep.setdefault("merged", []).append(other["ref"])


# ---------------------------------------------------------------- plan

def build_plan(cfg: Config, index: dict, profile: dict, role: dict, kmap: KmapIndex | None) -> dict:
    excluded: list[dict] = []
    projects: list[dict] = []

    gh_items: dict[str, dict] = {}
    for entry in index["manifests"]:
        ref = f"gh:{entry['key']}"
        if not entry.get("ok"):
            excluded.append({"ref": ref, "reason": "清单不合格：" + "; ".join(entry.get("errors", []))[:200]})
            continue
        if entry.get("status") not in IN_RESUME:
            excluded.append({"ref": ref, "reason": f"status: {entry.get('status')}，不进入简历"})
            continue
        gh_items[entry["key"]] = github_item(entry, load_manifest(cfg, index, entry), role, kmap)

    # The same project can live both in the profile and on GitHub (`repo:` links them). It appears once:
    # an internship stays under its experience and takes in the repo's facts; anything else becomes the
    # repo item, which takes in the profile's (often attested) facts.
    absorbed: set[str] = set()
    for p in profile.get("projects", []):
        ref = f"local:{p['id']}"
        if not p["verified"]:
            excluded.append({"ref": ref, "reason": "verified: false，事实和数字还没核对"})
            continue
        item = local_item(p, ref, role, kmap)
        if p.get("repo") in gh_items:
            _absorb(gh_items[p["repo"]], item, "profile")
            excluded.append({"ref": ref, "reason": f"与 gh:{p['repo']} 是同一项目，已并入"})
            continue
        projects.append(item)

    experience = []
    for exp in profile.get("experience", []):
        items = []
        for p in exp.get("projects", []):
            ref = f"exp:{exp['id']}/{p['id']}"
            if not p["verified"]:
                excluded.append({"ref": ref, "reason": "verified: false，事实和数字还没核对"})
                continue
            item = local_item(p, ref, role, kmap)
            if p.get("repo") in gh_items:
                _absorb(item, gh_items[p["repo"]], "repo")
                absorbed.add(p["repo"])
                excluded.append({"ref": f"gh:{p['repo']}", "reason": f"与实习项目 {ref} 是同一项目，已并入"})
            items.append(item)
        items.sort(key=lambda i: -i["score"])
        experience.append({"ref": f"exp:{exp['id']}", "org": exp["org"], "public_alias": exp.get("public_alias"),
                           "title": exp["title"], "period": exp["period"], "summary": exp.get("summary"),
                           "projects": items})

    warnings = [{"ref": f"gh:{key}", "code": "internship-not-linked",
                 "message": "清单 kind: internship，但没有任何实习经历用 repo: 关联它，会被当成独立项目；"
                            "在 profile.yaml 对应经历的 projects 里加一条 repo: " + key}
                for key, it in gh_items.items() if key not in absorbed and it.get("kind") == "internship"]
    projects += [it for key, it in gh_items.items() if key not in absorbed]
    projects.sort(key=lambda i: (-i["score"], i["title"]))
    limit = int(role.get("limits", {}).get("projects", 3))
    for i, p in enumerate(projects):
        p["selected"] = i < limit and p["score"] > 0
    return {
        "schema": "portfolio/plan@1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "login": index["login"],
        "role": role,
        "kmap": kmap.source if kmap else None,
        "profile": {k: profile.get(k) for k in ("name", "public_name", "public_github", "contact", "education",
                                                 "publications", "awards", "skills", "photo")}
                   | {"headline": role.get("headline") or profile.get("headline")},
        "experience": experience,
        "projects": projects,
        "excluded": excluded,
        "warnings": warnings,
        "limits": role.get("limits", {}),
        "coverage": coverage(role, projects, experience),
    }


def _text(item: dict) -> str:
    return (item["title"] + " " + " ".join(item.get("techs", [])) + " "
            + json.dumps(item["facts"], ensure_ascii=False)).lower()


def coverage(role: dict, projects: list[dict], experience: list[dict]) -> list[dict]:
    """For a role written from a JD: which plan items carry evidence for each requirement.
    A requirement with no evidence is a gap — to be said honestly, never papered over in the draft."""
    items = projects + [p for e in experience for p in e["projects"]]
    out = []
    for r in role.get("requirements") or []:
        kws = [k.lower() for k in r.get("keywords", []) if k]
        refs = [i["ref"] for i in items if any(k in _text(i) for k in kws)]
        out.append({"need": r["need"], "must": bool(r.get("must")), "keywords": r.get("keywords", []),
                    "evidence": refs})
    return out


def plan_path(cfg: Config, login: str, role_id: str) -> Path:
    return ensure_private(cfg.user_dir(login) / "plans" / f"{role_id}.json", "简历计划")


def save_plan(cfg: Config, plan: dict) -> Path:
    p = plan_path(cfg, plan["login"], plan["role"]["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_plan(cfg: Config, login: str, role_id: str) -> dict:
    p = plan_path(cfg, login, role_id)
    if not p.exists():
        raise PlanError(f"还没有 {role_id} 的计划：先运行 `profile-atlas plan --role {role_id}`")
    return json.loads(p.read_text(encoding="utf-8"))
