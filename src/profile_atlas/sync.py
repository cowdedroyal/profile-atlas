"""Pull every project manifest from the GitHub account `gh` is logged into.

Scans repositories owned by the user and by the organisations they belong to (or the ones listed in
config.yaml), finds every `.portfolio/project.yaml` at any depth (sandbox repos keep one per
subdirectory), validates its shape, and caches it under ~/.local/share/profile-atlas/<login>/.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .check import validate_data
from .config import Config, ensure_private, secure_dir

_TRANSIENT = ("EOF", "connection reset", "TLS handshake timeout", "i/o timeout", "502", "503")


class SyncError(Exception):
    pass


def gh(args: list[str]) -> str:
    """Run `gh` and return stdout; retries network-level failures."""
    for attempt in range(4):
        p = subprocess.run(["gh", *args], capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout
        if not any(t in p.stderr for t in _TRANSIENT) or attempt == 3:
            raise SyncError(f"gh {' '.join(args)}: {p.stderr.strip()[-300:]}")
        time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def _lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


REPO_JQ = ('.[] | {full: .full_name, owner: .owner.login, name: .name, private: .private, visibility: .visibility, '
           'archived: .archived, fork: .fork, stars: .stargazers_count, pushed_at: .pushed_at, '
           'branch: .default_branch, url: .html_url, topics: .topics, template: .is_template}')


def discover(cfg: Config, run=gh) -> tuple[str, list[dict]]:
    login = cfg.owner or run(["api", "user", "--jq", ".login"]).strip()
    orgs = cfg.orgs if cfg.orgs is not None else run(["api", "user/orgs", "--jq", ".[].login"]).split()
    repos = _lines(run(["api", "--paginate", "user/repos?affiliation=owner&per_page=100", "--jq", REPO_JQ]))
    for org in orgs:
        repos += _lines(run(["api", "--paginate", f"orgs/{org}/repos?type=all&per_page=100", "--jq", REPO_JQ]))
    seen, out = set(), []
    for r in repos:
        if r.get("template"):  # template repositories hold placeholder manifests, not projects
            continue
        if r["full"] not in seen:
            seen.add(r["full"])
            out.append(r)
    return login, out


def manifest_paths(repo: dict, run=gh) -> list[str]:
    try:
        out = run(["api", f"repos/{repo['full']}/git/trees/{repo['branch']}?recursive=1",
                   "--jq", '.tree[] | select(.type=="blob") | .path'])
    except SyncError as e:
        if "409" in str(e) or "Git Repository is empty" in str(e):
            return []
        raise
    return sorted(p for p in out.splitlines() if p == ".portfolio/project.yaml" or p.endswith("/.portfolio/project.yaml"))


def fetch(repo: dict, path: str, run=gh) -> str:
    b64 = run(["api", f"repos/{repo['full']}/contents/{path}?ref={repo['branch']}", "--jq", ".content"])
    return base64.b64decode(b64).decode("utf-8")


def sync(cfg: Config, run=gh, workers: int = 6) -> dict:
    login, repos = discover(cfg, run)
    root = secure_dir(cfg.user_dir(login))
    mdir = root / "manifests"
    # Download into a fresh directory and swap it in only when everything succeeded, so an
    # interrupted sync never leaves a half-empty cache next to an old index.
    tmp = root / "manifests.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()

    def one(repo: dict) -> list[dict]:
        rows = []
        for path in manifest_paths(repo, run):
            sub = path[: -len(".portfolio/project.yaml")].rstrip("/")
            key = f"{repo['full']}" + (f"/{sub}" if sub else "")
            text = fetch(repo, path, run)
            entry = {"key": key, "repo": repo["full"], "subdir": sub, "path": path,
                     "manifest_url": f"{repo['url']}/blob/{repo['branch']}/{path}",
                     "project_url": repo["url"] + (f"/tree/{repo['branch']}/{sub}" if sub else ""),
                     **{k: repo[k] for k in ("visibility", "private", "archived", "fork", "stars", "pushed_at", "branch", "topics")}}
            try:
                data = yaml.safe_load(text)
                report = validate_data(data, None, where=key)
                entry.update(ok=report.ok, errors=[i.message for i in report.issues if i.level == "error"],
                             id=data.get("id"), title=data.get("title"), status=data.get("status", "active"),
                             manifest_visibility=data.get("visibility"))
            except yaml.YAMLError as e:
                entry.update(ok=False, errors=[f"YAML: {e}"])
            fname = key.replace("/", "__") + ".yaml"
            (tmp / fname).write_text(text, encoding="utf-8")
            entry["file"] = fname
            rows.append(entry)
        return rows

    with ThreadPoolExecutor(workers) as pool:
        entries = [e for rows in pool.map(one, repos) for e in rows]
    entries.sort(key=lambda e: e["key"])
    index = {"login": login, "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "repos_scanned": len(repos), "manifests": entries}
    if mdir.exists():
        shutil.rmtree(mdir)
    tmp.rename(mdir)
    (root / "index.json.partial").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "index.json.partial").replace(root / "index.json")
    return index


def load_index(cfg: Config, login: str | None = None) -> dict:
    root = cfg.data_dir
    if login is None:
        cands = sorted(root.glob("*/index.json"), key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
        if not cands:
            raise SyncError("还没有同步过：先运行 `profile-atlas sync`")
        f = cands[0]
    else:
        f = cfg.user_dir(login) / "index.json"
        if not f.exists():
            raise SyncError(f"{login} 还没有同步过：先运行 `profile-atlas sync`")
    return json.loads(f.read_text(encoding="utf-8"))


def load_manifest(cfg: Config, index: dict, entry: dict) -> dict:
    return yaml.safe_load((cfg.user_dir(index["login"]) / "manifests" / entry["file"]).read_text(encoding="utf-8"))
