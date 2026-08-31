"""Model Deployment Web App — FastAPI entrypoint.

Routes:
  GET  /                 inventory catalog (filterable)
  POST /api/sync         enqueue a sync job -> {job_id}
  GET  /targets          target machine cards + connectivity + per-category sync
  GET  /downloads        HuggingFace GGUF download form + history
  POST /api/hf/download  enqueue an HF download job -> {job_id}
  GET  /jobs             live job queue (SSE progress)
  GET  /settings         edit config.yaml targets/path mappings
  API helpers: /api/inventory, /api/jobs, /api/job/<id>,
               /api/targets/status, /api/hf/list

Sync and HF-download jobs run on a single background worker thread so rsync/HF
output stays ordered. The UI polls /api/job/<id> or subscribes to the SSE stream
/api/stream/<job_id> for live output.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .categorize import categorize_repo
from .config import (CONFIG_PATH, Config, PathMapping, Source, TargetMachine, load_config)
from .jobs import JobStatus, queue
from .scanner import MANIFEST_NAME, scan_all
from .sync import local_disk_usage, remote_disk_usage, run_rsync, test_connection
from . import hf_download as hf
from . import model_store
from .model_store import apply_model_meta, get_or_default, key_for, owner
from .registry import (build_deploy_plan, cache_status_for_repo,
                       ensure_cached, get_provider)

app = FastAPI(title="Model Deployment")


@app.middleware("http")
async def _no_cache_html(request: Request, call_next):
    """Force browsers to revalidate HTML pages so template/JS edits show on refresh.

    The hub UI's rendering JS is inlined into the HTML, so a stale cached page renders
    stale UI. Set no-store only for HTML/text responses; leave static assets alone.
    """
    response = await call_next(request)
    ctype = response.headers.get("content-type", "")
    if ctype.startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def main():
    import uvicorn

    cfg = _cfg()
    uvicorn.run("app.main:app", host=cfg.bind_host, port=cfg.app_port)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Cache-buster: bump when templates/static assets change so browsers refetch (see
# _no_cache_html middleware + base.html app.js query). Derived from file mtimes.
def _app_version() -> str:
    mtime = max((p.stat().st_mtime for p in TEMPLATES_DIR.glob("*.html")), default=0)
    if STATIC_DIR.exists():
        for p in STATIC_DIR.glob("*"):
            mtime = max(mtime, p.stat().st_mtime)
    return f"{int(mtime)}"

# Background connectivity cache: {target_name: (ok, detail, checked_at)}
_conn_cache: dict[str, tuple[bool, str, float]] = {}
_conn_lock = threading.Lock()

# Disk usage snapshot: {"local": view|None, "targets": {name: {category: view}}}
_disk_cache: dict = {}
_disk_lock = threading.Lock()


def _disk_view(u):
    """Normalize a raw disk-usage dict for the UI (or None)."""
    if not u:
        return None
    pct = u.get("percent")
    return {"total": u["total_bytes"], "used": u["used_bytes"], "free": u["free_bytes"],
            "pct": pct}


def _refresh_disk():
    cfg = _cfg()
    cache_dir = getattr(getattr(cfg, "registry", None), "repo_root", None) or "."
    local = _disk_view(local_disk_usage(cache_dir))
    targets_out = {}
    for name, t in cfg.targets.items():
        # Group a target's categories by the filesystem they live on, so one disk
        # that hosts many categories shows up as a single gauge (not 10 identical
        # bars). Key = mount point when known, else fall back to the remote_root.
        by_fs: dict[str, dict] = {}
        for cat, pm in (t.categories or {}).items():
            u = remote_disk_usage(t.host, t.user, pm.remote_root, key=t.ssh_key)
            if not u:
                continue
            view = _disk_view(u)
            key = u.get("mounted") or pm.remote_root
            entry = by_fs.get(key)
            if entry is None:
                by_fs[key] = {**view, "mounted": key, "categories": [cat]}
            else:
                entry.setdefault("categories", []).append(cat)
        targets_out[name] = {"filesystems": list(by_fs.values())}
    with _disk_lock:
        global _disk_cache
        _disk_cache = {"local": local, "targets": targets_out}


def _disk_loop():
    while True:
        time.sleep(30)
        try:
            _refresh_disk()
        except Exception:
            pass


def _cfg() -> Config:
    return load_config(initialize=True)


# ---------------------------------------------------------------------------
# Catalog + model metadata
# ---------------------------------------------------------------------------

def _source_roots(cfg: Config) -> dict[str, str]:
    return {s.name: s.root for s in cfg.sources}


def _catalog(cfg: Config):
    """Scan the sources and merge in persisted metadata.

    Returns ``(models, meta_by_key)`` where ``models`` is hidden-filtered and
    name/category/quant overrides applied, and each model carries a ``meta``
    attribute plus a ``source_root`` attribute for update checks.
    """
    models = scan_all(cfg.sources)
    return apply_model_meta(models, source_root=_source_roots(cfg))


def _model_json(m) -> dict:
    """Serialize a scanned+metadata-enriched model into the API card shape."""
    import json

    meta = getattr(m, "meta", None) or {}
    stored_tags = meta.get("hf_tags")
    if isinstance(stored_tags, str):
        try:
            parsed = json.loads(stored_tags)
            tags = parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            tags = []
    elif isinstance(stored_tags, list):
        tags = stored_tags
    else:
        tags = []
    return {
        "name": m.name, "category": m.category, "quant": m.quant,
        "size_gb": m.size_gb, "file_count": m.file_count, "kind": m.kind,
        "files": m.files,
        "source": m.source, "path": m.path,
        "key": key_for(m.source, m.path),
        "display_name": meta.get("display_name"),
        "upstream_repo": meta.get("upstream_repo"),
        "has_upstream": bool(meta.get("upstream_repo")),
        "hf_tags": tags,
        "notes": meta.get("notes"),
        "update_status": meta.get("update_status"),
        "update_checked_at": meta.get("update_checked_at"),
        "hidden": meta.get("hidden", False),
    }


def _check_update(cfg: Config, key: str, meta: dict) -> dict:
    """Compare a model card's local files against its attributed upstream repo.

    Layout-agnostic: matches upstream file sizes to the local files by basename,
    so it works for both flat cache files and trees downloaded under a repo
    folder. Persists ``update_status`` / ``update_checked_at`` onto the record.
    """
    parsed = owner(key)
    if not parsed:
        return {"ok": False, "error": "bad model key"}
    repo = meta.get("upstream_repo")
    if not repo:
        return {"ok": False, "error": "no upstream repo attached"}

    model = None
    for m in scan_all(cfg.sources):
        if key_for(m.source, m.path) == key:
            model = m
            break
    if model is None:
        return {"ok": True, "status": "not-found", "repo": repo, "files": []}

    provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
    resolved = provider.resolve(repo)
    root = _source_roots(cfg).get(model.source, cfg.registry.repo_root)
    remote_sizes: dict[str, int | None] = {}
    try:
        remote_sizes = provider.list_files_with_sizes(resolved) or {}
    except Exception:
        remote_sizes = {}

    by_base: dict[str, int | None] = {}
    for rf, size in remote_sizes.items():
        by_base.setdefault(os.path.basename(rf).lower(), size)

    files = []
    for lf in model.files:
        try:
            local_size = os.path.getsize(os.path.join(root, lf))
        except OSError:
            local_size = None
        remote_size = by_base.get(os.path.basename(lf).lower())
        if local_size is None:
            st = "missing"
        elif remote_size is None:
            st = "unknown"
        elif remote_size == local_size:
            st = "up-to-date"
        else:
            st = "update-available"
        files.append({"file": lf, "status": st,
                      "size_local": local_size, "size_remote": remote_size})

    status = "up-to-date"
    if any(f["status"] == "update-available" for f in files):
        status = "update-available"
    elif any(f["status"] == "missing" for f in files):
        status = "not-cached"
    elif any(f["status"] == "unknown" for f in files):
        status = "unknown"

    import datetime as _dt
    model_store.save_internal(key, {"update_status": status,
                                    "update_checked_at": _dt.datetime.now(
                                        _dt.timezone.utc).isoformat(timespec="seconds")})
    return {"ok": True, "repo": resolved, "status": status,
            "update_available": status == "update-available", "files": files}


def _source_for_root(cfg: Config, root: str) -> str | None:
    """Map a repo-root directory back to its configured source name (or None)."""
    root = os.path.abspath(root)
    for s in cfg.sources:
        if os.path.abspath(s.root) == root:
            return s.name
    return None


def _record_download_meta(cfg: Config, summary: dict) -> None:
    """Persist the downloaded repo's upstream id + tags and write its co-located
    manifest so the files group into a single tree card on the next scan.

    The model_store key is derived from the source that owns ``repo_root`` plus the
    resolved repo id, matching exactly what the scanner produces for that folder —
    so a later metadata save or update check targets the same card. Best-effort: any
    failure is logged but never raised, since it must not break an otherwise-successful
    download job. The manifest sidecar lives inside the downloaded repo folder itself
    (``repo_root/<repo_id>/.modelmeta.json``), so each downloaded repo carries its own
    and none shadows another.
    """
    import datetime as _dt
    import json

    repo = summary.get("repo")
    if not repo:
        return
    source = _source_for_root(cfg, cfg.registry.repo_root)
    key = None if source is None else key_for(source, repo)
    manifest_dir = os.path.join(cfg.registry.repo_root, repo)
    try:
        os.makedirs(manifest_dir, exist_ok=True)
    except OSError as e:
        print(f"  !! could not create manifest dir for {repo}: {e}")
        return
    tags: list[str] = []
    try:
        meta = get_provider(cfg.registry.provider, cfg.registry.token_env).repo_meta(repo)
        tags = list(meta.get("tags", []) or [])
    except Exception as e:
        print(f"  !! could not fetch upstream meta for {repo}: {e}")
    manifest = {"repo_id": repo, "model_name": hf.model_name_from_repo(repo),
                "tags": tags,
                "saved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
    try:
        with open(os.path.join(manifest_dir, MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
    except OSError as e:
        print(f"  !! could not write manifest for {repo}: {e}")
    if key is None:
        return
    try:
        model_store.save(key, {"upstream_repo": repo, "hf_tags": json.dumps(tags)},
                         provider=cfg.registry.provider)
    except Exception as e:
        print(f"  !! could not persist meta for {repo}: {e}")


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

def _runner(job, emit):
    """Execute a queued job. Called by the worker thread."""
    if job.kind == "sync":
        m = job.meta
        selected = m.get("selected")

        # Whole-tree sync (default): one rsync of the source path.
        if not selected:
            result = run_rsync(
                host=m["host"], user=m["user"], local_path=m["local_path"],
                remote_root=m["remote_root"], key=m.get("key"), ntfs=m.get("ntfs", False),
                on_output=lambda line: emit(line), cancel_hook=lambda: queue.is_cancelled(job.id),
                on_progress=lambda frac: job.set_progress(frac * 100),
            )
            job.result = {"returncode": result.returncode, "summary": result.summary,
                          "duration_s": round(result.duration_s, 1)}
            return

        # Selected-file sync (grouped card): push each chosen file to where it would
        # land if the whole tree were rsynced — remote_root/<file relative to the
        # synced folder>, so structure is preserved and nothing gets nested extra.
        files = [s for s in selected if os.path.exists(s["local"])]
        total = len(files) or 1
        synced = failed = skipped = 0
        start = time.time()
        for i, entry in enumerate(selected):
            if queue.is_cancelled(job.id):
                break
            local_path = entry["local"]
            rel = entry["rel"]
            if m.get("dry_run"):
                emit(f"[dry-run] would sync {rel} -> {m['remote_root']}")
                skipped += 1
            elif not os.path.exists(local_path):
                emit(f"[missing] {local_path}")
                failed += 1
            else:
                res = run_rsync(
                    host=m["host"], user=m["user"], local_path=local_path,
                    remote_root=m["remote_root"], key=m.get("key"), ntfs=m.get("ntfs", False),
                    remote_subpath=rel,
                    on_output=lambda line: emit(line),
                    cancel_hook=lambda: queue.is_cancelled(job.id),
                    on_progress=lambda frac: job.set_progress(
                        round(60 + (frac * 40) * ((i + 1) / total), 1)),
                )
                if res.ok:
                    synced += 1
                    emit(f"[synced] {rel}")
                else:
                    failed += 1
                    emit(f"[failed] {rel} rc={res.returncode}")
            job.set_progress(round(60 + 40 * ((i + 1) / total), 1))
        job.result = {"ok": failed == 0, "synced": synced, "skipped": skipped,
                      "failed": failed, "dry_run": m.get("dry_run", False),
                      "duration_s": round(time.time() - start, 1)}
    elif job.kind == "hf_download":
        m = job.meta
        cfg = _cfg()

        def step(s):
            emit(f"[{s.get('action')}] {s.get('local', s.get('remote_file', s.get('note','')))}")

        summary = hf.run_hf_download(
            model=m["model"], quants=m["quants"], output_dir=m["output_dir"],
            token_env=m.get("token_env", "HF_TOKEN"), dry_run=m.get("dry_run", False),
            no_mmproj=m.get("no_mmproj", False), mmproj=m.get("mmproj"), on_step=step,
            check_staleness=True,
            should_cancel=lambda: queue.is_cancelled(job.id),
            on_progress=lambda frac: job.set_progress(frac * 100),
        )
        job.result = summary
        # Persist upstream repo id + tags and write the co-located manifest so the
        # downloaded files group into a single tree card on the next scan. Skipped for
        # dry-runs (nothing was really written) or failed jobs.
        if summary.get("ok") and not summary.get("dry_run"):
            _record_download_meta(cfg, summary)
    elif job.kind == "deploy":
        m = job.meta
        cfg = _cfg()
        provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
        repo_root = m["output_dir"]

        def step(s):
            emit(f"[{s.get('action')}] {s.get('local', s.get('remote_file', s.get('note','')))}")

        plan = build_deploy_plan(provider, m["model"], m["quants"], m["no_mmproj"],
                                 m.get("mmproj"))
        if not plan["plan"]:
            raise RuntimeError(f"{plan['repo_id']}: no matching files found")

        # Decide the destination path mapping on the target.
        cat = m.get("category") or categorize_repo(
            plan["model_name"],
            [os.path.basename(p["remote_file"]) if p["kind"] == "model" else p["local_name"]
             for p in plan["plan"]],
        )
        tm = cfg.targets.get(m["target"])
        pm = (tm.categories or {}).get(cat) if tm else None
        if not tm or not pm:
            raise RuntimeError(f"no path mapping for category {cat!r} on target {m['target']!r}")
        remote_root = pm.remote_root
        emit(f"deploying [{', '.join(p['local_name'] for p in plan['plan'])}] "
             f"-> {m['target']} ({cat}) to {remote_root}")

        # Phase 1: ensure files are in the server cache (refresh stale ones, download the rest).
        should_cancel = lambda: queue.is_cancelled(job.id)  # noqa: E731
        total = len(plan["plan"]) or 1
        local_paths = ensure_cached(provider, plan, repo_root,
                                    dry_run=m.get("dry_run", False), on_step=step,
                                    check_staleness=True, should_cancel=should_cancel,
                                    on_progress=lambda frac: job.set_progress(round(frac * 60, 1)))

        # Phase 2: push each cached file to the target, mirroring HF structure.
        synced = failed = skipped = 0
        sync_total = len(local_paths) or 1
        for i, lp in enumerate(local_paths):
            if should_cancel():
                break
            rel = os.path.relpath(lp, repo_root)
            if m.get("dry_run"):
                emit(f"[dry-run] would sync {rel} -> {remote_root}")
                skipped += 1
            elif not os.path.exists(lp):
                emit(f"[missing] {lp}")
                failed += 1
            else:
                res = run_rsync(host=m["host"], user=m["user"], local_path=lp,
                                remote_root=remote_root, key=m.get("key"),
                                ntfs=m.get("ntfs", False), remote_subpath=rel,
                                on_output=lambda line: emit(line), cancel_hook=should_cancel,
                                on_progress=lambda frac: job.set_progress(
                                    round(60 + (frac * 40) * ((i + 1) / sync_total), 1)))
                if res.ok:
                    synced += 1
                    emit(f"[synced] {rel}")
                else:
                    failed += 1
                    emit(f"[failed] {os.path.basename(lp)} rc={res.returncode}")
            # Each phase-2 file moves the bar from 60% toward 100%.
            job.set_progress(round(60 + 40 * ((i + 1) / sync_total), 1))

        job.result = {"ok": failed == 0, "repo": plan["repo_id"], "category": cat,
                      "target": m["target"], "remote_root": remote_root,
                      "cached_or_downloaded": len(local_paths), "synced": synced,
                      "skipped": skipped, "failed": failed, "dry_run": m.get("dry_run", False)}


def _start_workers():
    queue.start_worker(_runner)
    threading.Thread(target=_conn_loop, daemon=True).start()
    threading.Thread(target=_disk_loop, daemon=True).start()


def _conn_loop():
    while True:
        time.sleep(15)
        cfg = _cfg()
        for name in cfg.targets:
            t = cfg.targets[name]
            try:
                ok, detail = test_connection(t.host, t.user, t.ssh_key)
            except Exception as e:
                ok, detail = False, str(e)
            with _conn_lock:
                _conn_cache[name] = (ok, detail, time.time())


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _startup():
    _start_workers()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    cfg = _cfg()
    models, _meta = _catalog(cfg)
    categories = sorted({m.category for m in models})
    quants = sorted({m.quant for m in models if m.quant})
    deploy_targets = {
        name: {"categories": {cat: pm.remote_root for cat, pm in (t.categories or {}).items()},
               "host": t.host, "user": t.user}
        for name, t in cfg.targets.items()
    }
    sources_json = [{"name": s.name, "root": s.root} for s in cfg.sources]
    resp = TEMPLATES.TemplateResponse(request, "index.html", {"active": "index", "models": models,
                                      "sources": sources_json, "categories": categories,
                                      "quants": quants, "cfg": cfg,
                                      "deploy_targets": deploy_targets,
                                      "all_categories": sorted(set(categories)),
                                      "app_version": _app_version()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/targets", response_class=HTMLResponse)
def targets(request: Request):
    cfg = _cfg()
    with _conn_lock:
        cache = dict(_conn_cache)
    with _disk_lock:
        disk_cache = dict(_disk_cache)
    targets_json = {
        name: {"categories": {cat: pm.remote_root for cat, pm in (t.categories or {}).items()}}
        for name, t in cfg.targets.items()
    }
    resp = TEMPLATES.TemplateResponse(request, "targets.html", {"active": "targets", "cfg": cfg,
                                       "conn_cache": cache, "disk_cache": disk_cache, "targets_json": targets_json,
                                       "app_version": _app_version()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/downloads", response_class=HTMLResponse)
def downloads(request: Request):
    cfg = _cfg()
    recent = [j for j in queue.all() if j.kind in ("hf_download", "deploy")]
    all_cats = sorted({cat for t in cfg.targets.values() for cat in (t.categories or {})})
    with _disk_lock:
        disk_cache = dict(_disk_cache)
    deploy_targets = {
        name: {"categories": {cat: pm.remote_root for cat, pm in (t.categories or {}).items()}}
        for name, t in cfg.targets.items()
    }
    resp = TEMPLATES.TemplateResponse(request, "downloads.html", {"active": "downloads", "cfg": cfg,
                                       "recent": recent[-10:], "targets": cfg.targets,
                                       "deploy_targets": deploy_targets,
                                       "all_categories": all_cats,
                                       "disk_cache": disk_cache,
                                       "app_version": _app_version()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request):
    resp = TEMPLATES.TemplateResponse(request, "jobs.html", {"active": "jobs", "jobs":
                                      queue.all(), "app_version": _app_version()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    cfg = _cfg()
    import yaml
    data = {
        "sources": [asdict(s) for s in cfg.sources],
        "targets": {k: asdict(v) for k, v in cfg.targets.items()},
        "registry": asdict(cfg.registry),
        "app_port": cfg.app_port,
        "bind_host": cfg.bind_host,
    }
    yaml_text = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    resp = TEMPLATES.TemplateResponse(request, "settings.html", {"active": "settings", "cfg": cfg,
                                      "yaml_text": yaml_text, "config_path": str(CONFIG_PATH),
                                      "app_version": _app_version()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------------------
# API — inventory
# ---------------------------------------------------------------------------

@app.get("/api/inventory")
def api_inventory(source: str | None = None, category: str | None = None,
                  quant: str | None = None):
    cfg = _cfg()
    models, _meta = _catalog(cfg)
    if source:
        models = [m for m in models if m.source == source]
    if category:
        models = [m for m in models if m.category == category]
    if quant:
        models = [m for m in models if m.quant == quant]
    return {
        "count": len(models),
        "models": [_model_json(m) for m in models],
    }


# ---------------------------------------------------------------------------
# API — hub browser (upstream search) + local cache
# ---------------------------------------------------------------------------

@app.get("/api/hub/search")
def api_hub_search(q: str = "", gguf: str = "", pipeline: str | None = None,
                   cursor: str | None = None, limit: int = 12):
    cfg = _cfg()
    provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
    if not hasattr(provider, "search"):
        return JSONResponse({"results": [], "next_cursor": None, "has_more": False,
                             "error": f"{provider.id} does not support hub search"}, status_code=400)
    return provider.search(q, limit=limit, cursor=cursor,
                           gguf_only=gguf.strip().lower() in ("1", "true", "yes", "on"),
                           pipeline=pipeline)


@app.get("/api/cache")
def api_cache(category: str | None = None, query: str = ""):
    """The locally-downloaded cache (scanned from config sources)."""
    cfg = _cfg()
    models, _meta = _catalog(cfg)
    if category:
        models = [m for m in models if m.category == category]
    if query:
        q = query.lower()
        models = [m for m in models if q in m.name.lower() or q in m.path.lower()]
    models = sorted(models, key=lambda m: (m.category, m.name.lower()))
    return {
        "count": len(models),
        "categories": sorted({m.category for m in models}),
        "models": [_model_json(m) for m in models],
    }


# ---------------------------------------------------------------------------
# API — sync jobs
# ---------------------------------------------------------------------------

@app.post("/api/sync")
def api_sync(source_root: str = Form(...), local_path: str = Form(...),
             target: str = Form(...), remote_root: str = Form(...),
             category: str = Form(""), files: list[str] = Form([])):
    cfg = _cfg()
    tm = cfg.targets.get(target)
    if not tm:
        return JSONResponse({"ok": False, "error": f"unknown target {target}"}, status_code=400)
    local_abs = os.path.join(source_root, local_path.lstrip("/"))

    # A grouped (tree) card can carry an explicit subset of files to push. Each
    # entry is repo-relative from the source root; we strip the synced folder's
    # prefix so a file lands under remote_root at exactly where it would if the
    # whole tree were rsynced verbatim (structure preserved, nothing nested extra).
    selected = None
    if files:
        tree_rel = local_path.strip("/")
        selected = []
        for rel in files:
            r = rel.strip("/").replace("\\", "/")
            if tree_rel and r.startswith(tree_rel + "/"):
                within = r[len(tree_rel) + 1:]
            else:
                within = os.path.basename(r)
            selected.append({"local": os.path.abspath(os.path.join(source_root, r)),
                             "rel": within})

    label = ", ".join(s["rel"] for s in (selected or [])) or os.path.basename(local_path)
    job = queue.create("sync", f"{label} -> {target}")
    job.meta = {  # type: ignore[attr-defined]
        "host": tm.host, "user": tm.user, "key": tm.ssh_key, "ntfs": tm.ntfs,
        "local_path": local_abs, "remote_root": remote_root, "category": category,
        "selected": selected,
    }
    return {"ok": True, "job_id": job.id}


# ---------------------------------------------------------------------------
# API — HF downloads
# ---------------------------------------------------------------------------

@app.get("/api/hf/list")
def api_hf_list(repo: str):
    cfg = _cfg()
    provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
    resolved = provider.resolve(repo)
    files = provider.list_files(resolved)
    return {"repo": resolved, "files": files}


@app.get("/api/hub/files")
def api_hub_files(repo: str):
    """List available GGUF files for a repo (drawer file/quant picker)."""
    cfg = _cfg()
    provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
    resolved = provider.resolve(repo)
    files = provider.list_files(resolved)
    sizes: dict[str, int | None] = {}
    try:
        sizes = provider.list_files_with_sizes(resolved) or {}
    except NotImplementedError:
        pass
    except Exception:
        # Sizes are best-effort; a failed lookup shouldn't fail the whole listing.
        sizes = {}
    cache_status = cache_status_for_repo(provider, resolved, cfg.registry.repo_root)
    return {"repo": resolved, "files": files, "sizes": sizes, "cacheStatus": cache_status}


def _prune_empty_dirs(root_abs: str, touched_abs: list[str]) -> None:
    """Remove ancestor directories of the removed files that are now empty, deepest
    first so parents become removable once their children are gone. Stops at
    ``root_abs`` — never removes a source root itself."""
    root_abs = os.path.abspath(root_abs)
    dirs: set[str] = set()
    for fp in touched_abs:
        d = os.path.dirname(fp)
        while d and d != root_abs and d.startswith(root_abs + os.sep):
            dirs.add(d)
            d = os.path.dirname(d)
    for d in sorted(dirs, key=len, reverse=True):
        try:
            if not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass


def _remove_cache_files(root_abs: str, rel_files: list[str]) -> dict:
    """Delete exactly ``rel_files`` (repo-relative paths under ``root_abs``) and prune
    any directories that become empty as a result. Only the listed paths are removed, so
    sibling models in the same folder are never touched. Returns freed bytes, the deleted
    relative paths, and any per-file errors."""
    freed = 0
    deleted: list[str] = []
    errors: list[dict] = []
    abs_files = [os.path.abspath(os.path.join(root_abs, rel)) for rel in rel_files]
    for fp in abs_files:
        try:
            if not os.path.lexists(fp):
                continue
            freed += os.path.getsize(fp) if os.path.isfile(fp) else 0
            os.remove(fp)
            deleted.append(os.path.relpath(fp, root_abs))
        except OSError as e:
            errors.append({"path": os.path.relpath(fp, root_abs), "error": str(e)})
    _prune_empty_dirs(root_abs, abs_files)
    return {"freed_bytes": freed, "deleted": deleted, "errors": errors}


def _manifest_candidates(rel_paths: list[str]) -> list[str]:
    """Candidate sidecar manifest paths for a multi-file group being deleted,
    innermost first.

    The manifest lives in the files' common directory, or an ancestor of it when the
    files sit in subfolders of the downloaded repo. Returns [] for a single-file card;
    the caller removes the first candidate that exists, so an unrelated manifest is
    never touched.
    """
    if len(rel_paths) < 2:
        return []
    try:
        common_dir = os.path.commonpath(rel_paths)
    except ValueError:
        return []
    candidates: list[str] = []
    d = common_dir
    while True:
        candidates.append(os.path.join(d, MANIFEST_NAME))
        parent = os.path.dirname(d)
        if parent in ("", ".", d):
            break
        d = parent
    return candidates


@app.post("/api/cache/delete")
def api_cache_delete(key: str = Form("")):
    """Permanently remove a cached model's files from disk. The model is resolved from a
    fresh scan by its metadata key (so the path can't be forged), then exactly its files
    are deleted and any emptied directories pruned. Its metadata overlay is cleared too."""
    cfg = _cfg()
    roots = _source_roots(cfg)
    root: str | None = None
    for m in scan_all(cfg.sources):
        if key and key_for(m.source, m.path) == key:
            root = roots.get(m.source, cfg.registry.repo_root)
            break
    if not root or not key:
        return JSONResponse({"ok": False, "error": "model not found in cache"}, status_code=404)
    result = _remove_cache_files(root, m.files)
    # A tree card may carry a co-located manifest sidecar; remove it too so the repo
    # folder is fully emptied and can be pruned afterwards. Model files are handled
    # first, so only sibling models would keep the folder alive.
    manifest_rel = next(
        (rel for rel in _manifest_candidates(m.files)
         if os.path.lexists(os.path.join(root, rel))),
        None,
    )
    if manifest_rel is not None:
        abs_manifest = os.path.abspath(os.path.join(root, manifest_rel))
        try:
            result["freed_bytes"] += os.path.getsize(abs_manifest)
            result["deleted"].append(manifest_rel)
            os.remove(abs_manifest)
        except OSError as e:
            result["errors"].append({"path": manifest_rel, "error": str(e)})
        _prune_empty_dirs(root, [abs_manifest])
    model_store.delete(key)
    if result["errors"]:
        return {"ok": True, "freed_bytes": result["freed_bytes"],
                "deleted": len(result["deleted"]), "errors": result["errors"]}
    return {"ok": True, "freed_bytes": result["freed_bytes"], "deleted": len(result["deleted"])}


@app.post("/api/cache/group")
def api_cache_group(folder: str = Form(""), repo: str = Form(""), name: str = Form("")):
    """Manually group a cached folder into a single model card by writing the
    ``.modelmeta.json`` sidecar into it. Everything the scanner sees under the folder
    (including subfolders) then collapses into one tree card on the next scan —
    the same mechanism a fresh download uses, so re-grouping stays consistent."""
    import datetime as _dt
    import json

    cfg = _cfg()
    rel = folder.strip().strip("/").replace("\\", "/")
    if not rel:
        return JSONResponse({"ok": False, "error": "folder is required"}, status_code=400)
    target: str | None = None
    owner_name: str | None = None
    for src in cfg.sources:
        root = os.path.abspath(src.root)
        cand = os.path.abspath(os.path.join(root, rel))
        if cand == root or not cand.startswith(root + os.sep):
            continue
        if os.path.isdir(cand):
            target, owner_name = cand, src.name
            break
    if target is None:
        return JSONResponse({"ok": False, "error": "folder not found in any source"},
                            status_code=404)
    repo_id = repo.strip() or rel
    tags: list[str] = []
    if repo.strip():
        # Best-effort: tags must never block grouping (the first hub call in a
        # fresh process can stall on DNS/connection, so bound it hard).
        try:
            from concurrent.futures import ThreadPoolExecutor

            ex = ThreadPoolExecutor(max_workers=1)
            try:
                fut = ex.submit(get_provider(cfg.registry.provider, cfg.registry.token_env)
                                .repo_meta, repo_id)
                meta = fut.result(timeout=15)
            finally:
                ex.shutdown(wait=False)
            tags = list(meta.get("tags", []) or [])
        except Exception:
            tags = []
    existing: dict = {}
    manifest_path = os.path.join(target, MANIFEST_NAME)
    if os.path.lexists(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                existing = json.load(fh)
        except (OSError, ValueError):
            existing = {}
    payload = {
        "repo_id": repo_id,
        "model_name": name.strip() or existing.get("model_name")
                        or hf.model_name_from_repo(repo_id),
        "tags": tags or existing.get("tags") or [],
        "saved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except OSError as e:
        return JSONResponse({"ok": False, "error": f"could not write manifest: {e}"},
                            status_code=500)
    key = None if owner_name is None else key_for(owner_name, rel)
    if key is not None:
        try:
            # Only persist upstream_repo when the user explicitly named one — a bare
            # folder path is not a usable HF repo id for update checks.
            model_store.save(key, {"upstream_repo": repo.strip() or None,
                                   "hf_tags": json.dumps(tags) if tags else None},
                             provider=cfg.registry.provider)
        except Exception as e:
            print(f"  !! could not persist meta for grouped folder {rel}: {e}")
    return {"ok": True, "folder": rel, "repo_id": repo_id}


@app.get("/api/cache/status")
def api_cache_status(repo: str):
    """Per-file cache status for a repo: which GGUFs are cached and whether an
    update is available (local size differs from the hub's)."""
    cfg = _cfg()
    provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
    resolved = provider.resolve(repo)
    return {"repo": resolved, "status": cache_status_for_repo(provider, resolved, cfg.registry.repo_root)}


@app.get("/api/cache/files")
def api_cache_files(key: str = ""):
    """Per-file listing for a local cache model (the drawer file picker). Returns the
    card's repo-relative files and their local sizes so grouped ("tree") cards can show
    exactly which files make them up — resolved from a fresh scan by metadata key, so the
    path can't be forged."""
    cfg = _cfg()
    if not key:
        return JSONResponse({"ok": False, "error": "bad model key"}, status_code=400)
    root = None
    model = None
    for m in scan_all(cfg.sources):
        if key_for(m.source, m.path) == key:
            model = m
            root = _source_roots(cfg).get(m.source, cfg.registry.repo_root)
            break
    if model is None or not root:
        return JSONResponse({"ok": False, "error": "model not found in cache"}, status_code=404)
    sizes: dict[str, int | None] = {}
    for rel in model.files:
        try:
            sizes[rel] = os.path.getsize(os.path.join(root, rel))
        except OSError:
            sizes[rel] = None
    return {"key": key, "files": model.files, "sizes": sizes}


# ---------------------------------------------------------------------------
# API — per-model editable metadata
# ---------------------------------------------------------------------------

@app.get("/api/model")
def api_model_get(key: str = ""):
    """The editable metadata record for a model card (defaults when empty)."""
    if not key:
        return {}
    return get_or_default(key)


@app.post("/api/model/update")
def api_model_update(key: str = Form(...), display_name: str = Form(""),
                     category: str = Form(""), quant: str = Form(""),
                     upstream_repo: str = Form(""), notes: str = Form(""),
                     hidden: str = Form("")):
    """Save the editable metadata for a model card (upsert by key)."""
    fields = {k: (v or None) for k, v in {
        "display_name": display_name, "category": category, "quant": quant,
        "upstream_repo": upstream_repo, "notes": notes,
    }.items()}
    fields["hidden"] = bool(hidden)
    saved = model_store.save(key, fields, provider=_cfg().registry.provider)
    return {"ok": True, "meta": saved}


@app.post("/api/model/delete")
def api_model_delete(key: str = Form(...)):
    """Clear a model card's metadata (back to fully auto-derived)."""
    return {"ok": True, "removed": model_store.delete(key)}


@app.post("/api/model/check-update")
def api_model_check_update(key: str = Form(...)):
    """Compare a model card against its attributed upstream repo and store the
    result, so it can also be shown without a live hub round-trip."""
    cfg = _cfg()
    meta = model_store.get(key)
    if not meta or not meta.get("upstream_repo"):
        return {"ok": False, "error": "no upstream repo attached"}
    return _check_update(cfg, key, meta)


@app.post("/api/hf/download")
def api_hf_download(model: str = Form(...), quants: list[str] = Form([]),
                    no_mmproj: bool = Form(False), dry_run: bool = Form(False),
                    mmproj: str = Form("")):
    cfg = _cfg()
    output_dir = cfg.registry.repo_root
    job = queue.create("hf_download", f"HF download {model} [{', '.join(quants)}]")
    job.meta = {  # type: ignore[attr-defined]
        "model": model, "quants": quants, "output_dir": output_dir,
        "token_env": cfg.registry.token_env, "dry_run": dry_run, "no_mmproj": no_mmproj,
        "mmproj": mmproj,
    }
    return {"ok": True, "job_id": job.id}


@app.post("/api/deploy")
def api_deploy(model: str = Form(...), quants: list[str] = Form([]),
               no_mmproj: bool = Form(False), dry_run: bool = Form(False),
               target: str = Form(""), category: str = Form(""),
               mmproj: str = Form("")):
    cfg = _cfg()
    output_dir = cfg.registry.repo_root
    if not target:
        # No target chosen -> just populate the cache (same as a download).
        job = queue.create("hf_download", f"HF download {model} [{', '.join(quants)}]")
        job.meta = {  # type: ignore[attr-defined]
            "model": model, "quants": quants, "output_dir": output_dir,
            "token_env": cfg.registry.token_env, "dry_run": dry_run, "no_mmproj": no_mmproj,
            "mmproj": mmproj,
        }
        return {"ok": True, "job_id": job.id, "mode": "cache"}
    tm = cfg.targets.get(target)
    if not tm:
        return JSONResponse({"ok": False, "error": f"unknown target {target}"}, status_code=400)
    job = queue.create("deploy", f"Deploy {model} [{', '.join(quants)}] -> {target}")
    job.meta = {  # type: ignore[attr-defined]
        "model": model, "quants": quants, "output_dir": output_dir,
        "token_env": cfg.registry.token_env, "dry_run": dry_run, "no_mmproj": no_mmproj,
        "mmproj": mmproj,
        "target": target, "category": category, "host": tm.host, "user": tm.user,
        "key": tm.ssh_key, "ntfs": tm.ntfs,
    }
    return {"ok": True, "job_id": job.id, "mode": "deploy"}


# ---------------------------------------------------------------------------
# API — jobs + connectivity
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def api_jobs():
    # no-store: a tunnel/proxy/browser could otherwise cache an empty {jobs:[]} from
    # before the job existed and keep serving it, so /jobs looks blank while downloads run.
    return JSONResponse(
        {"jobs": [j.to_dict() for j in queue.all()]},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = queue.get(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse(job.to_dict(), headers={"Cache-Control": "no-store"})


@app.post("/api/job/{job_id}/cancel")
def api_job_cancel(job_id: str):
    ok = queue.cancel_job(job_id)
    if not ok:
        return JSONResponse(
            {"ok": False, "error": "not found or already terminal"}, status_code=409)
    return JSONResponse({"ok": True})


@app.post("/api/job/{job_id}/restart")
def api_job_restart(job_id: str):
    job = queue.get(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return JSONResponse(
            {"ok": False, "error": "cannot restart a job that is queued or running"},
            status_code=409)
    new = queue.create(job.kind, f"Restarted: {job.description}")
    new.meta = dict(job.meta) if job.meta else {}
    return JSONResponse({"ok": True, "job_id": new.id})


@app.get("/api/targets/status")
def api_targets_status():
    with _conn_lock:
        out = {k: {"ok": ok, "detail": d, "checked_at": t} for k, (ok, d, t) in _conn_cache.items()}
    return out


@app.get("/api/disk")
def api_disk():
    with _disk_lock:
        return JSONResponse(dict(_disk_cache), headers={"Cache-Control": "no-store"})


@app.get("/api/stream/{job_id}")
def api_stream(job_id: str):
    """SSE stream of a job's log lines as they arrive."""
    def gen():
        last = 0
        while True:
            job = queue.get(job_id)
            if not job:
                yield f"data: {{'type':'gone'}}\n\n"
                return
            log = getattr(job, "log", [])
            if len(log) > last:
                for line in log[last:]:
                    import json as _json
                    yield f"data: {_json.dumps({'line': line})}\n\n"
                last = len(log)
            d = job.to_dict()
            yield f"data: {_json.dumps({'status': d['status'], 'progress': d['progress']})}\n\n"
            if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
                import json as _json
                yield f"data: {_json.dumps({'final': job.to_dict()})}\n\n"
                return
            time.sleep(0.4)
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# API — settings save
# ---------------------------------------------------------------------------

@app.post("/api/settings/save")
def api_settings_save(cfg_yaml: str = Form(...)):
    import yaml
    cfg = _cfg()
    data = yaml.safe_load(cfg_yaml) or {}
    cfg.targets = {}
    for name, t in (data.get("targets") or {}).items():
        cats = {}
        for cat, pm in (t.get("categories") or {}).items():
            cats[cat] = PathMapping(
                remote_root=pm["remote_root"], subdirs=pm.get("subdirs", {}))
        cfg.targets[name] = TargetMachine(**{**t, "categories": cats})
    try:
        cfg.save()
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ---------------------------------------------------------------------------
# PWA — service worker + manifest
# ---------------------------------------------------------------------------

# Service worker must be served with a JavaScript content-type and no-store so an
# updated sw.js isn't held in HTTP cache (a stale SW would block the new one from
# controlling pages). The /static mount never intercepts this path.
@app.get("/sw.js", response_class=PlainTextResponse)
def service_worker():
    return FileResponse(str(STATIC_DIR / "sw.js"), media_type="text/javascript",
                        headers={"Cache-Control": "no-store, max-age=0"})


# Web app manifest — served explicitly so it carries application/manifest+json (the
# content-type Chrome requires; the /static mount can't guarantee that for .webmanifest).
@app.get("/manifest.webmanifest", response_class=PlainTextResponse)
def web_manifest():
    return FileResponse(str(STATIC_DIR / "manifest.webmanifest"),
                        media_type="application/manifest+json",
                        headers={"Cache-Control": "no-store, max-age=0"})


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
