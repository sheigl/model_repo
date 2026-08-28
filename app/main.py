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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .categorize import categorize_repo
from .config import (CONFIG_PATH, Config, PathMapping, Source, TargetMachine, load_config)
from .jobs import JobStatus, queue
from .scanner import scan_all
from .sync import run_rsync, test_connection
from . import hf_download as hf
from .registry import build_deploy_plan, ensure_cached, get_provider

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


def _cfg() -> Config:
    return load_config(initialize=True)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

def _runner(job, emit):
    """Execute a queued job. Called by the worker thread."""
    if job.kind == "sync":
        m = job.meta
        result = run_rsync(
            host=m["host"], user=m["user"], local_path=m["local_path"],
            remote_root=m["remote_root"], key=m.get("key"), ntfs=m.get("ntfs", False),
            on_output=lambda line: emit(line),
        )
        job.result = {"returncode": result.returncode, "summary": result.summary,
                      "duration_s": round(result.duration_s, 1)}
    elif job.kind == "hf_download":
        m = job.meta

        def step(s):
            emit(f"[{s.get('action')}] {s.get('remote_file', s.get('note',''))}")

        summary = hf.run_hf_download(
            model=m["model"], quants=m["quants"], output_dir=m["output_dir"],
            token_env=m.get("token_env", "HF_TOKEN"), dry_run=m.get("dry_run", False),
            no_mmproj=m.get("no_mmproj", False), on_step=step,
        )
        job.result = summary
    elif job.kind == "deploy":
        m = job.meta
        cfg = _cfg()
        provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
        repo_root = m["output_dir"]

        def step(s):
            emit(f"[{s.get('action')}] {s.get('remote_file', s.get('note',''))}")

        plan = build_deploy_plan(provider, m["model"], m["quants"], m["no_mmproj"])
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

        # Phase 1: ensure files are in the server cache (download what's missing).
        local_paths = ensure_cached(provider, plan, repo_root,
                                    dry_run=m.get("dry_run", False), on_step=step)

        # Phase 2: push each cached file to the target, mirroring HF structure.
        synced = failed = skipped = 0
        for lp in local_paths:
            rel = os.path.relpath(lp, repo_root)
            if m.get("dry_run"):
                emit(f"[dry-run] would sync {rel} -> {remote_root}")
                skipped += 1
                continue
            if not os.path.exists(lp):
                emit(f"[missing] {lp}")
                failed += 1
                continue
            res = run_rsync(host=m["host"], user=m["user"], local_path=lp,
                            remote_root=remote_root, key=m.get("key"),
                            ntfs=m.get("ntfs", False), remote_subpath=rel,
                            on_output=lambda line: emit(line))
            if res.ok:
                synced += 1
                emit(f"[synced] {rel}")
            else:
                failed += 1
                emit(f"[failed] {os.path.basename(lp)} rc={res.returncode}")

        job.result = {"ok": failed == 0, "repo": plan["repo_id"], "category": cat,
                      "target": m["target"], "remote_root": remote_root,
                      "cached_or_downloaded": len(local_paths), "synced": synced,
                      "skipped": skipped, "failed": failed, "dry_run": m.get("dry_run", False)}


def _start_workers():
    queue.start_worker(_runner)
    threading.Thread(target=_conn_loop, daemon=True).start()


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
    models = scan_all(cfg.sources)
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
    targets_json = {
        name: {"categories": {cat: pm.remote_root for cat, pm in (t.categories or {}).items()}}
        for name, t in cfg.targets.items()
    }
    resp = TEMPLATES.TemplateResponse(request, "targets.html", {"active": "targets", "cfg": cfg,
                                      "conn_cache": cache, "targets_json": targets_json,
                                      "app_version": _app_version()})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/downloads", response_class=HTMLResponse)
def downloads(request: Request):
    cfg = _cfg()
    recent = [j for j in queue.all() if j.kind in ("hf_download", "deploy")]
    all_cats = sorted({cat for t in cfg.targets.values() for cat in (t.categories or {})})
    deploy_targets = {
        name: {"categories": {cat: pm.remote_root for cat, pm in (t.categories or {}).items()}}
        for name, t in cfg.targets.items()
    }
    resp = TEMPLATES.TemplateResponse(request, "downloads.html", {"active": "downloads", "cfg": cfg,
                                      "recent": recent[-10:], "targets": cfg.targets,
                                      "deploy_targets": deploy_targets,
                                      "all_categories": all_cats,
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
    models = scan_all(_cfg().sources)
    if source:
        models = [m for m in models if m.source == source]
    if category:
        models = [m for m in models if m.category == category]
    if quant:
        models = [m for m in models if m.quant == quant]
    return {
        "count": len(models),
        "models": [{
            "name": m.name, "category": m.category, "quant": m.quant,
            "size_gb": m.size_gb, "file_count": m.file_count, "kind": m.kind,
            "source": m.source, "path": m.path,
        } for m in models],
    }


# ---------------------------------------------------------------------------
# API — hub browser (upstream search) + local cache
# ---------------------------------------------------------------------------

@app.get("/api/hub/search")
def api_hub_search(q: str = "", gguf: bool = False, cursor: str | None = None,
                   limit: int = 12):
    cfg = _cfg()
    provider = get_provider(cfg.registry.provider, cfg.registry.token_env)
    if not hasattr(provider, "search"):
        return JSONResponse({"results": [], "next_cursor": None, "has_more": False,
                             "error": f"{provider.id} does not support hub search"}, status_code=400)
    return provider.search(q, limit=limit, cursor=cursor, gguf_only=gguf)


@app.get("/api/cache")
def api_cache(category: str | None = None, query: str = ""):
    """The locally-downloaded cache (scanned from config sources)."""
    cfg = _cfg()
    models = scan_all(cfg.sources)
    if category:
        models = [m for m in models if m.category == category]
    if query:
        q = query.lower()
        models = [m for m in models if q in m.name.lower() or q in m.path.lower()]
    models = sorted(models, key=lambda m: (m.category, m.name.lower()))
    return {
        "count": len(models),
        "categories": sorted({m.category for m in models}),
        "models": [{
            "name": m.name, "category": m.category, "quant": m.quant,
            "size_gb": m.size_gb, "file_count": m.file_count, "kind": m.kind,
            "source": m.source, "path": m.path,
        } for m in models],
    }


# ---------------------------------------------------------------------------
# API — sync jobs
# ---------------------------------------------------------------------------

@app.post("/api/sync")
def api_sync(source_root: str = Form(...), local_path: str = Form(...),
             target: str = Form(...), remote_root: str = Form(...),
             category: str = Form("")):
    cfg = _cfg()
    tm = cfg.targets.get(target)
    if not tm:
        return JSONResponse({"ok": False, "error": f"unknown target {target}"}, status_code=400)
    local_abs = os.path.join(source_root, local_path.lstrip("/"))
    job = queue.create("sync", f"{os.path.basename(local_path)} -> {target}")
    job.meta = {  # type: ignore[attr-defined]
        "host": tm.host, "user": tm.user, "key": tm.ssh_key, "ntfs": tm.ntfs,
        "local_path": local_abs, "remote_root": remote_root, "category": category,
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
    return {"repo": resolved, "files": files, "sizes": sizes}


@app.post("/api/hf/download")
def api_hf_download(model: str = Form(...), quants: list[str] = Form(...),
                    no_mmproj: bool = Form(False), dry_run: bool = Form(False)):
    cfg = _cfg()
    output_dir = cfg.registry.repo_root
    job = queue.create("hf_download", f"HF download {model} [{', '.join(quants)}]")
    job.meta = {  # type: ignore[attr-defined]
        "model": model, "quants": quants, "output_dir": output_dir,
        "token_env": cfg.registry.token_env, "dry_run": dry_run, "no_mmproj": no_mmproj,
    }
    return {"ok": True, "job_id": job.id}


@app.post("/api/deploy")
def api_deploy(model: str = Form(...), quants: list[str] = Form(...),
               no_mmproj: bool = Form(False), dry_run: bool = Form(False),
               target: str = Form(""), category: str = Form("")):
    cfg = _cfg()
    output_dir = cfg.registry.repo_root
    if not target:
        # No target chosen -> just populate the cache (same as a download).
        job = queue.create("hf_download", f"HF download {model} [{', '.join(quants)}]")
        job.meta = {  # type: ignore[attr-defined]
            "model": model, "quants": quants, "output_dir": output_dir,
            "token_env": cfg.registry.token_env, "dry_run": dry_run, "no_mmproj": no_mmproj,
        }
        return {"ok": True, "job_id": job.id, "mode": "cache"}
    tm = cfg.targets.get(target)
    if not tm:
        return JSONResponse({"ok": False, "error": f"unknown target {target}"}, status_code=400)
    job = queue.create("deploy", f"Deploy {model} [{', '.join(quants)}] -> {target}")
    job.meta = {  # type: ignore[attr-defined]
        "model": model, "quants": quants, "output_dir": output_dir,
        "token_env": cfg.registry.token_env, "dry_run": dry_run, "no_mmproj": no_mmproj,
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


@app.get("/api/targets/status")
def api_targets_status():
    with _conn_lock:
        out = {k: {"ok": ok, "detail": d, "checked_at": t} for k, (ok, d, t) in _conn_cache.items()}
    return out


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
            if job.status in (JobStatus.DONE, JobStatus.FAILED):
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


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
