"""Where things live, and the settings that differ between users.

Nothing personal is hard-coded in the tool. Personal data and generated resumes live in per-user
directories outside any git repository:

    ~/.config/profile-atlas/config.yaml     optional settings (below)
    ~/.config/profile-atlas/profile.yaml    contact, education, experience — private, never committed
    ~/.config/profile-atlas/roles/*.yaml    your own role definitions (e.g. written from a JD)
    ~/.local/share/profile-atlas/           synced manifests, plans, drafts, rendered resumes

config.yaml (every key optional):

    owner: <github login>           # default: the account `gh` is logged in as
    orgs: [my-lab]                  # organisations to scan; default: all you belong to
    kmap: <url or file>             # knowledge-map graph.json; "none" to disable
    intake: {org: my-lab, workspace: ~/lab, template: my-lab/project-template}
    chrome: /path/to/chrome         # for PDF export; default: auto-detect
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("PROFILE_ATLAS_CONFIG", Path.home() / ".config" / "profile-atlas")).expanduser()
DATA_DIR = Path(os.environ.get("PROFILE_ATLAS_DATA", Path.home() / ".local" / "share" / "profile-atlas")).expanduser()
DEFAULT_KMAP = "https://cowdedroyal.github.io/data/graph.json"


class PrivacyError(Exception):
    """Raised when private data would end up inside a git repository."""


def git_toplevel(path: Path) -> Path | None:
    p = path if path.is_dir() else path.parent
    while not p.exists():
        p = p.parent
    r = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(r.stdout.strip()) if r.returncode == 0 else None


def ensure_private(path: Path, what: str) -> Path:
    """Refuse to read or write private data inside a git work tree, where it could be committed."""
    path = path.expanduser().resolve()
    top = git_toplevel(path)
    if top is not None:
        raise PrivacyError(f"{what} 位于 git 仓库 {top} 内：隐私数据不能放进任何仓库，请移到仓库之外（默认 {CONFIG_DIR}）")
    return path


def secure_dir(path: Path) -> Path:
    """Create a private directory (0700) outside any repository, tightening the data root as well."""
    path = ensure_private(path, "数据目录")
    path.mkdir(parents=True, exist_ok=True)
    for p in (path, DATA_DIR, CONFIG_DIR):
        if p.exists() and (p == path or p in path.parents):
            os.chmod(p, 0o700)
    return path


@dataclass
class Config:
    owner: str | None = None
    orgs: list[str] | None = None
    kmap: str = DEFAULT_KMAP
    intake_org: str | None = None
    workspace: Path = field(default_factory=lambda: Path.home() / "lab")
    template: str | None = None
    chrome: str | None = None
    config_dir: Path = CONFIG_DIR
    data_dir: Path = DATA_DIR

    @property
    def profile_path(self) -> Path:
        return self.config_dir / "profile.yaml"

    @property
    def roles_dir(self) -> Path:
        return self.config_dir / "roles"

    def user_dir(self, login: str) -> Path:
        return self.data_dir / login


def load_config(config_dir: Path | None = None, data_dir: Path | None = None) -> Config:
    cdir = (config_dir or CONFIG_DIR).expanduser()
    raw: dict = {}
    f = cdir / "config.yaml"
    if f.exists():
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    intake = raw.get("intake") or {}
    cfg = Config(
        owner=raw.get("owner"),
        orgs=raw.get("orgs"),
        kmap=os.environ.get("PROFILE_ATLAS_KMAP", raw.get("kmap", DEFAULT_KMAP)),
        intake_org=os.environ.get("PROFILE_ATLAS_ORG", intake.get("org")),
        workspace=Path(os.environ.get("PROFILE_ATLAS_WORKSPACE", intake.get("workspace", Path.home() / "lab"))).expanduser(),
        template=os.environ.get("PROFILE_ATLAS_TEMPLATE", intake.get("template")),
        chrome=raw.get("chrome"),
        config_dir=cdir,
        data_dir=(data_dir or DATA_DIR).expanduser(),
    )
    return cfg
