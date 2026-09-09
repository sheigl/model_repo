"""Custom model packages: named groups of files from one or more remote repos,
fetched into the local cache exactly like single-file hub downloads.

A package is stored in the same SQLite DB as the inventory (see
model_store.py) and keeps its components as a JSON list:

    [{"label": "base", "repo": "org/repo", "files": ["a.safetensors", ...]}, ...]

Local file location follows the existing per-repo cache layout
(repo_root/<repo>/<file>) so inventory scans, cache-status endpoints and
delete/group actions all keep working on package-downloaded files.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from typing import Callable, Optional

from . import model_store


# ---------- store ----------

def _connect() -> sqlite3.Connection:
    # model_store lazily creates its schema; import-time _DB_PATH is fine to
    # reuse because tests may reassign it before any call happens.
    model_store._connect()
    return sqlite3.connect(model_store._DB_PATH)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS model_packages (
               id TEXT PRIMARY KEY,
               name TEXT NOT NULL UNIQUE,
               task TEXT NOT NULL DEFAULT '',
               description TEXT NOT NULL DEFAULT '',
               components TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    conn.execute("PRAGMA journal_mode=WAL")


def _row_to_pkg(row) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "task": row[2],
        "description": row[3],
        "components": json.loads(row[4]),
        "created_at": row[5],
        "updated_at": row[6],
    }


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "package"


def _normalize_components(components: list) -> list:
    if not isinstance(components, list) or not components:
        raise ValueError("at least one component is required")
    out = []
    for c in components:
        if not isinstance(c, dict):
            raise ValueError("each component must be an object")
        repo = str(c.get("repo") or "").strip().strip("/")
        if not repo:
            raise ValueError("component repo is required")
        files = c.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"component {repo}: at least one file is required")
        norm_files = []
        for f in files:
            f = str(f).strip().lstrip("/")
            if not f or ".." in f.split("/") or f.split("/")[0].startswith("."):
                raise ValueError(f"component {repo}: invalid file path {f!r}")
            f = f.replace("\\", "/")
            if f not in norm_files:
                norm_files.append(f)
        label = str(c.get("label") or "").strip() or (
            repo.rsplit("/", 1)[-1] if len(norm_files) > 1 else os.path.basename(norm_files[0])
        )
        out.append({"label": label, "repo": repo, "files": norm_files})
    return out


def create_package(name: str, task: str, description: str, components: list) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("package name is required")
    if len(name) > 120:
        raise ValueError("package name too long")
    comps = _normalize_components(components)
    conn = _connect()
    try:
        _ensure_table(conn)
        cur = conn.execute("SELECT id FROM model_packages WHERE lower(name)=lower(?)", (name,))
        if cur.fetchone():
            raise ValueError(f"package {name!r} already exists")
        pid = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        conn.execute(
            "INSERT INTO model_packages (id, name, task, description, components) "
            "VALUES (?,?,?,?,?)",
            (pid, name, str(task or ""), str(description or ""), json.dumps(comps)),
        )
        conn.commit()
        return get_package(pid)
    finally:
        conn.close()


def list_packages() -> list:
    conn = _connect()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT id,name,task,description,components,created_at,updated_at "
            "FROM model_packages ORDER BY lower(name)"
        ).fetchall()
        return [_row_to_pkg(r) for r in rows]
    finally:
        conn.close()


def get_package(pid: str) -> Optional[dict]:
    conn = _connect()
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT id,name,task,description,components,created_at,updated_at "
            "FROM model_packages WHERE id=?", (pid,)
        ).fetchone()
        return _row_to_pkg(row) if row else None
    finally:
        conn.close()


def update_package(pid: str, name: str, task: str, description: str, components: list) -> Optional[dict]:
    name = str(name or "").strip()
    if not name:
        raise ValueError("package name is required")
    if len(name) > 120:
        raise ValueError("package name too long")
    comps = _normalize_components(components)
    conn = _connect()
    try:
        _ensure_table(conn)
        cur = conn.execute("SELECT id FROM model_packages WHERE lower(name)=lower(?) AND id!=?", (name, pid))
        if cur.fetchone():
            raise ValueError(f"package {name!r} already exists")
        res = conn.execute(
            "UPDATE model_packages SET name=?, task=?, description=?, components=?, "
            "updated_at=datetime('now') WHERE id=?",
            (name, str(task or ""), str(description or ""), json.dumps(comps), pid),
        )
        conn.commit()
        return get_package(pid) if res.rowcount else None
    finally:
        conn.close()


def delete_package(pid: str) -> bool:
    conn = _connect()
    try:
        _ensure_table(conn)
        res = conn.execute("DELETE FROM model_packages WHERE id=?", (pid,))
        conn.commit()
        return res.rowcount > 0
    finally:
        conn.close()


# ---------- status / fetch ----------

def _flatten(pkg: dict) -> list:
    items = []
    for c in pkg["components"]:
        for f in c["files"]:
            items.append((c["repo"], f, c["label"]))
    return items


def status_for(pkg: dict, repo_root: str, provider) -> dict:
    """Per-file local cache + remote freshness status for a package."""
    from .registry import cache_status_for_repo

    repos = []
    for c in pkg["components"]:
        if c["repo"] not in repos:
            repos.append(c["repo"])
    cache = {}
    for repo in repos:
        try:
            cache[repo] = cache_status_for_repo(provider, repo, repo_root)
        except Exception:
            cache[repo] = {}

    components = []
    counts = {"cached": 0, "update_available": 0, "missing": 0,
              "bytes_local": 0, "bytes_remote": 0, "total": 0}
    for c in pkg["components"]:
        repo = c["repo"]
        st = cache.get(repo, {})
        entries = []
        for f in c["files"]:
            local = os.path.join(repo_root, repo, f)
            exists = os.path.isfile(local)
            info = st.get(f) or {}
            size_remote = info.get("size_remote")
            size_local = os.path.getsize(local) if exists else 0
            if not exists:
                state = "missing"
            elif size_remote is None:
                # Remote size unknown: can't tell, treat as cached.
                state = "cached"
            elif info.get("up_to_date"):
                state = "cached"
            else:
                state = "update-available"
            if state == "cached":
                counts["cached"] += 1
            elif state == "update-available":
                counts["update_available"] += 1
            else:
                counts["missing"] += 1
            counts["bytes_local"] += size_local
            counts["bytes_remote"] += size_remote if size_remote is not None else (size_local if exists else 0)
            counts["total"] += 1
            entries.append({"file": f, "state": state, "size_local": size_local,
                            "size_remote": size_remote})
        components.append({"label": c["label"], "repo": repo, "files": entries})
    return {"package_id": pkg["id"], "components": components, "counts": counts}


def fetch_package(pkg: dict, repo_root: str, provider, dry_run: bool = False,
                  check_staleness: bool = True,
                  on_step: Optional[Callable[[dict], None]] = None,
                  should_cancel: Optional[Callable[[], bool]] = None,
                  on_progress: Optional[Callable[[float], None]] = None) -> dict:
    """Download every package file that is missing (or stale) into the cache."""
    from .registry import _download_and_place

    items = _flatten(pkg)
    total = len(items)
    cached = downloaded = refreshed = 0
    errors = []
    for i, (repo, fname, _label) in enumerate(items):
        if should_cancel is not None and should_cancel():
            raise RuntimeError("cancelled")
        local_path = os.path.join(repo_root, repo, fname)
        exists = os.path.isfile(local_path)
        stale = False
        if exists and check_staleness:
            try:
                from .registry import cache_status_for_repo
                info = (cache_status_for_repo(provider, repo, repo_root) or {}).get(fname) or {}
                # Matches ensure_cached: only refresh when the remote size is
                # known and differs from the local copy.
                stale = bool(info.get("cached")) and info.get("size_remote") is not None \
                    and not bool(info.get("up_to_date"))
            except Exception:
                stale = False
        if exists and not stale:
            cached += 1
            if on_step:
                on_step({"action": "cached", "repo": repo, "local": fname})
        elif dry_run:
            if on_step:
                on_step({"action": "dry-run", "repo": repo, "local": fname,
                         "note": "stale" if stale else "missing"})
        else:
            if on_step:
                on_step({"action": "downloading", "repo": repo, "local": fname})
            try:
                _download_and_place(provider, repo, fname, local_path, repo_root,
                                    local_name=fname, on_step=on_step)
                if stale:
                    refreshed += 1
                else:
                    downloaded += 1
            except Exception as e:
                errors.append({"repo": repo, "file": fname, "error": str(e)})
                if on_step:
                    on_step({"action": "failed", "repo": repo, "local": fname, "note": str(e)})
        if on_progress:
            on_progress((i + 1) / total)
    ok = not errors
    return {"ok": ok, "dry_run": dry_run, "total": total, "cached": cached,
            "downloaded": downloaded, "refreshed": refreshed, "errors": errors}
