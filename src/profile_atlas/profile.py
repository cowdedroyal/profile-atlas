"""The private, local-only profile: ~/.config/profile-atlas/profile.yaml."""
from __future__ import annotations

import json
import os
import stat
from importlib import resources
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from .config import Config, ensure_private


class ProfileError(Exception):
    pass


def profile_schema() -> dict:
    return json.loads(resources.files("profile_atlas").joinpath("schema/profile.schema.json").read_text("utf-8"))


def validate_profile(data: object) -> list[str]:
    errors = sorted(Draft202012Validator(profile_schema()).iter_errors(data), key=lambda e: list(e.path))
    out = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
    if not out and isinstance(data, dict):
        ids = [p["id"] for p in data.get("projects", [])]
        for exp in data.get("experience", []):
            ids.append(exp["id"])
            ids += [f"{exp['id']}/{p['id']}" for p in exp.get("projects", [])]
        dups = {i for i in ids if ids.count(i) > 1}
        out += [f"id 重复：{i}" for i in sorted(dups)]
    return out


def load_profile(cfg: Config, path: Path | None = None) -> dict:
    p = ensure_private(path or cfg.profile_path, "个人资料")
    if not p.exists():
        raise ProfileError(f"找不到 {p}：先运行 `profile-atlas profile init`")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    errors = validate_profile(data)
    if errors:
        raise ProfileError("个人资料不合格：\n" + "\n".join(errors))
    return data


def permission_warning(path: Path) -> str | None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        return f"{path} 对其他用户可读，建议 chmod 600"
    return None


SKELETON = """\
# 个人资料：只在本机，永远不要提交到任何仓库。生成简历时与 GitHub 上的项目清单合并。
# 校验：profile-atlas profile check
schema: portfolio/profile@1
name: 你的名字
contact: {phone: "", email: "", github: ""}
education:
  - {school: 学校, degree: 硕士, major: 专业, start: "2025-09", end: "2028-07", details: []}
experience:                       # 实习与工作：公司代码不能上 GitHub，事实写在这里
  - id: company-2026
    org: 公司全称
    public_alias: 某公司            # 公开版里用它代替公司名
    title: 岗位
    period: {start: "2026-04", end: "2026-08"}
    summary: 一句话职责
    projects:
      - id: main-project
        title: 项目名
        kind: internship
        facts: ["可以写进简历的事实；数字只能来自这里或 metrics"]
        metrics: []
        verified: false            # 和原始记录核对过之后改成 true，否则不会进入简历
projects: []                      # 不在 GitHub 上的竞赛、科研、个人项目，格式同上
publications: []
awards: []
"""


def init_profile(cfg: Config, force: bool = False) -> Path:
    p = ensure_private(cfg.profile_path, "个人资料")
    if p.exists() and not force:
        raise ProfileError(f"{p} 已存在；用 --force 覆盖")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(SKELETON, encoding="utf-8")
    os.chmod(p, 0o600)
    return p
