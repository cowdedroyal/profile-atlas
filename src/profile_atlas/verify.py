"""Check an agent-written resume draft against its plan. The writing is the agent's; the facts are not.

- every item refers to a plan item (unverified or excluded items are not in the plan, so they can't be used)
- every number in a title, tagline or bullet can be found in that item's facts (more precise is fine)
- numbers from smoke-scale metrics are never presented as improvements
"""
from __future__ import annotations

import json
import re
from importlib import resources

from jsonschema import Draft202012Validator

from .check import Issue, number_in

_NUM = re.compile(r"\d+(?:\.\d+)?")
_IMPROVE = re.compile(r"提升|提高|优于|超过|超越|增长|改善|显著|领先|翻倍|↑|→|->|from .* to|improv|outperform|beat", re.I)


def resume_schema() -> dict:
    return json.loads(resources.files("profile_atlas").joinpath("schema/resume.schema.json").read_text("utf-8"))


def numbers(text: str) -> list[str]:
    return _NUM.findall(text.replace(",", "").replace("，", "，"))


def _flatten(x) -> list[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, (int, float)):
        return [repr(x)]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _flatten(v)]
    if isinstance(x, list):
        return [s for v in x for s in _flatten(v)]
    return []


def corpus(item: dict) -> str:
    return "\n".join(_flatten([item["title"], item.get("period"), item["facts"]])).replace(",", "")


def _missing_numbers(text: str, pool: str) -> list[str]:
    return [n for n in numbers(text) if not number_in(pool, float(n))]


class Report:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def add(self, level: str, where: str, code: str, message: str) -> None:
        self.issues.append(Issue(level, where, code, message))

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def as_dict(self) -> dict:
        return {"ok": self.ok,
                "errors": [i.as_dict() for i in self.issues if i.level == "error"],
                "warnings": [i.as_dict() for i in self.issues if i.level == "warning"]}


def verify(plan: dict, draft: dict) -> Report:
    r = Report()
    for e in sorted(Draft202012Validator(resume_schema()).iter_errors(draft), key=lambda e: list(e.path)):
        r.add("error", "/".join(map(str, e.path)) or "<root>", "schema", e.message)
    if not r.ok:
        return r
    if draft["role"] != plan["role"]["id"]:
        r.add("error", "role", "role-mismatch", f"草稿是 {draft['role']}，计划是 {plan['role']['id']}")

    prof = plan["profile"]
    profile_pool = "\n".join(_flatten([prof.get("education"), prof.get("awards"), prof.get("publications")])).replace(",", "")
    everything = profile_pool + "\n" + "\n".join(
        corpus(i) for i in plan["projects"] + [p for e in plan["experience"] for p in e["projects"]])
    limit = int(plan.get("limits", {}).get("bullets", 3))

    for key in ("headline", "summary"):
        if draft.get(key):
            for n in _missing_numbers(draft[key], everything):
                r.add("error", key, "number-not-in-facts", f"{n} 在任何事实里都找不到")

    edu = prof.get("education") or []
    for i, e in enumerate(draft.get("education", [])):
        idx = int(e["ref"].split(":")[1])
        if idx >= len(edu):
            r.add("error", f"education[{i}]", "ref-unknown", f"没有 {e['ref']}")
            continue
        pool = "\n".join(_flatten(edu[idx])).replace(",", "")
        for d in e.get("details", []):
            for n in _missing_numbers(d, pool):
                r.add("error", f"education[{i}]", "number-not-in-facts", f"{n} 不在这段教育经历的资料里：{d[:40]}")

    by_ref = {p["ref"]: p for p in plan["projects"]}
    exps = {e["ref"]: e for e in plan["experience"]}

    def check_item(item: dict, plan_item: dict, where: str) -> None:
        pool = corpus(plan_item)
        for field in ("title", "tagline"):
            for n in _missing_numbers(item.get(field) or "", pool):
                r.add("error", f"{where}.{field}", "number-not-in-facts", f"{n} 在 {plan_item['ref']} 的事实里找不到")
        smoke = [v for m in plan_item["facts"].get("metrics", []) if m.get("scale") == "smoke"
                 for v in (m["value"], (m.get("baseline") or {}).get("value")) if v is not None]
        if len(item["bullets"]) > limit:
            r.add("warning", where, "too-many-bullets", f"{len(item['bullets'])} 条，岗位建议不超过 {limit} 条")
        for j, b in enumerate(item["bullets"]):
            w = f"{where}.bullets[{j}]"
            for n in _missing_numbers(b, pool):
                r.add("error", w, "number-not-in-facts", f"{n} 在 {plan_item['ref']} 的事实里找不到：{b[:50]}")
            if any(number_in(b, float(v)) for v in smoke) and _IMPROVE.search(b):
                r.add("error", w, "smoke-as-improvement", f"smoke 级结果被写成了效果提升：{b[:50]}")
            if len(b) > 120:
                r.add("warning", w, "bullet-too-long", f"{len(b)} 字，单页简历建议不超过 120 字")

    for i, p in enumerate(draft.get("projects", [])):
        pi = by_ref.get(p["ref"])
        if pi is None:
            r.add("error", f"projects[{i}]", "ref-unknown", f"计划里没有 {p['ref']}（可能未核实、已归档或不存在）")
            continue
        if not pi.get("selected"):
            r.add("warning", f"projects[{i}]", "not-selected", f"{p['ref']} 不在本岗位的推荐列表里（得分 {pi['score']}）")
        check_item(p, pi, f"projects[{i}]")

    for i, e in enumerate(draft.get("experience", [])):
        pe = exps.get(e["ref"])
        if pe is None:
            r.add("error", f"experience[{i}]", "ref-unknown", f"计划里没有 {e['ref']}")
            continue
        sub = {p["ref"]: p for p in pe["projects"]}
        pool = "\n".join(corpus(p) for p in pe["projects"]) + "\n" + (pe.get("summary") or "")
        for n in _missing_numbers(e.get("summary") or "", pool):
            r.add("error", f"experience[{i}].summary", "number-not-in-facts", f"{n} 在这段经历的事实里找不到")
        for j, p in enumerate(e["projects"]):
            pi = sub.get(p["ref"])
            if pi is None:
                r.add("error", f"experience[{i}].projects[{j}]", "ref-unknown", f"{e['ref']} 下没有 {p['ref']}（可能未核实）")
                continue
            check_item(p, pi, f"experience[{i}].projects[{j}]")
    return r
