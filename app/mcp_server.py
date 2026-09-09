"""MCP server for the model deployment app.

Exposes hub search, cache browsing, download/deploy job submission, package
management, job status/cancel, targets and disk usage to AI agents over the
Model Context Protocol. The server is mounted into the FastAPI app at
``cfg.mcp.path`` (default ``/mcp``); see ``main._build_mcp``.

Every tool that starts long-running background work (download_model, deploy_model,
package_fetch) **queues a job and returns immediately** with its ``job_id`` —
agents must poll ``job_status(job_id)`` until the status is ``done``/``failed``/
``cancelled``. This mirrors the web API exactly and lets the single background
worker keep downloads/rsync ordering intact.

All tools return structured output (TypedDict models) so agents receive clean
``structured_content`` rather than free-text JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, NotRequired, Optional, Tuple, TypedDict, Union

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from . import packages
from .config import Config, load_config
from .jobs import queue
from .registry import cache_status_for_repo, get_provider

INSTRUCTIONS = (
    "You manage a local AI model cache and can deploy models to remote targets. "
    "Typical workflow: 1) hub_search() to find a model on the hub, 2) hub_files() "
    "to inspect its files/quants and local cache status, 3) download_model() to "
    "populate the local cache or deploy_model() to push to a target (pick one with "
    "target_list()), 4) poll job_status(job_id) until status is 'done' or 'failed'. "
    "cache_list() browses what is already cached, and the package_* tools manage "
    "named groups of files from multiple repos fetched together. Never claim a "
    "download/deploy succeeded until job_status reports status='done'."
)


def _cfg() -> Config:
    return load_config(initialize=True)


def _provider(cfg: Config):
    return get_provider(cfg.registry.provider, cfg.registry.token_env)


# ---------------------------------------------------------------------------
# Structured-output models (TypedDict). Returned tools are wrapped so agents get
# clean structured_content alongside the JSON text.
# ---------------------------------------------------------------------------

class HubRepo(TypedDict):
    id: str
    author: Optional[str]
    downloads: int
    likes: int
    pipeline: Optional[str]
    tags: List[str]
    last_modified: Optional[str]


class HubSearchResult(TypedDict):
    results: List[HubRepo]
    next_cursor: Optional[str]
    has_more: bool


class HubFilesResult(TypedDict):
    repo: str
    files: List[str]
    sizes: dict
    cacheStatus: dict


class CacheModel(TypedDict):
    name: str
    category: str
    quant: Optional[str]
    size_gb: float
    file_count: int
    kind: str
    files: List[str]
    source: str
    path: str
    key: str
    display_name: Optional[str]
    upstream_repo: Optional[str]
    update_status: Optional[str]
    notes: Optional[str]


class CacheListResult(TypedDict):
    count: int
    categories: List[str]
    models: List[CacheModel]


class CacheStatusResult(TypedDict):
    repo: str
    status: dict


class JobInfo(TypedDict):
    id: str
    kind: str
    description: str
    status: str
    progress: float
    log: List[str]
    result: dict
    created_at: float
    started_at: Optional[float]
    finished_at: Optional[float]


class JobListResult(TypedDict):
    jobs: List[JobInfo]


class JobStatusResult(JobInfo):
    pass


class JobActionResult(TypedDict):
    ok: bool


class SubmitResult(TypedDict):
    ok: bool
    job_id: str
    mode: str


class TargetCategory(TypedDict):
    remote_root: str
    subdirs: dict


class TargetInfo(TypedDict):
    host: str
    user: str
    ntfs: bool
    ssh_key: Optional[str]
    categories: dict


class TargetListResult(TypedDict):
    targets: dict


class TargetStatusResult(TypedDict):
    """{target: {ok, detail, checked_at}}."""

    targets: dict


class DiskUsageResult(TypedDict):
    local: Optional[dict]
    targets: dict


class Package(TypedDict):
    id: str
    name: str
    task: str
    description: str
    components: List[dict]
    created_at: str
    updated_at: str


class PackageListResult(TypedDict):
    packages: List[Package]


class PackageResult(TypedDict):
    ok: bool
    package: Package


class PackageStatusResult(TypedDict):
    ok: bool
    package_id: str
    components: List[dict]
    counts: dict


class PackageDeleteResult(TypedDict):
    ok: bool
    removed: str


# ---------------------------------------------------------------------------
# Server + mount factory
# ---------------------------------------------------------------------------

def create_mcp_server(cfg: Config | None = None) -> MCPServer:
    """Build the MCP server with all tools registered.

    ``cfg`` is accepted for parity with ``build_mcp_app``; tools always read the
    current config at call time so config edits via the Settings page take effect
    without a restart (same behavior as the web API).
    """
    del cfg  # tools reload config lazily
    mcp = MCPServer("model-repo", instructions=INSTRUCTIONS)
    _register_tools(mcp)
    return mcp


@dataclass
class MCPMount:
    """The mountable MCP sub-app plus the session manager the host must run."""

    app: Any  # Starlette app (possibly wrapped by auth/CORS middleware)
    session_manager: Any  # entered by the host app's lifespan


def build_mcp_app(cfg: Config) -> MCPMount:
    """Build the mountable MCP sub-app + its session manager.

    Because a mounted sub-app's own lifespan never runs, the host FastAPI app
    must enter ``session_manager.run()`` from its own lifespan (see
    ``main._build_mcp`` / ``main._lifespan``), or every /mcp request fails with
    "Task group is not initialized".
    """
    mcp = create_mcp_server(cfg)

    if cfg.mcp.trusted_hosts:
        security = TransportSecuritySettings(allowed_hosts=cfg.mcp.trusted_hosts)
    else:
        # The app binds 0.0.0.0 and is reached via a dynamic container IP, so the
        # SDK's localhost-only default allowlist would reject everything with 421.
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    app = mcp.streamable_http_app(
        streamable_http_path="/",
        transport_security=security,
        json_response=True,
    )

    if cfg.mcp.auth_token:
        from .mcp_auth import BearerAuthMiddleware
        app = BearerAuthMiddleware(app, cfg.mcp.auth_token)

    if cfg.mcp.allow_origins:
        from starlette.middleware.cors import CORSMiddleware
        app = CORSMiddleware(
            app,
            allow_origins=cfg.mcp.allow_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=[
                "Authorization", "Content-Type", "Last-Event-ID",
                "Mcp-Method", "Mcp-Name", "Mcp-Protocol-Version", "Mcp-Session-Id",
            ],
            expose_headers=["Mcp-Session-Id"],
        )

    return MCPMount(app=app, session_manager=mcp.session_manager)


# ---------------------------------------------------------------------------
# Tools — hub + cache
# ---------------------------------------------------------------------------

def _register_tools(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=True)
    def hub_search(query: str = "", gguf_only: bool = False,
                   pipeline: str | None = None, cursor: str | None = None,
                   limit: int = 12) -> HubSearchResult:
        """Search the model hub for repositories.

        query: free-text search (empty = popular models sorted by downloads),
        gguf_only: restrict to GGUF repos, pipeline: filter by pipeline tag
        (e.g. "text-generation", "text-to-image").
        Returns repo cards (id, author, downloads, likes, pipeline, tags,
        last_modified) plus has_more for cursor pagination.
        """
        cfg = _cfg()
        provider = _provider(cfg)
        if not hasattr(provider, "search"):
            raise ToolError(f"provider {getattr(provider, 'id', '?')} does not support hub search")
        r = provider.search(query, limit=limit, cursor=cursor,
                            gguf_only=gguf_only, pipeline=pipeline)
        return {"results": r.get("results", []), "next_cursor": r.get("next_cursor"),
                "has_more": bool(r.get("has_more", False))}

    @mcp.tool(structured_output=True)
    def hub_files(repo: str) -> HubFilesResult:
        """List the files in a hub repository, with remote sizes and per-file cache
        status. repo: repo id (e.g. "unsloth/Llama-3.2-3B-Instruct-GGUF"); a bare
        name is resolved to the default GGUF namespace. cacheStatus per file:
        missing / cached (server up to date) / update-available (remote size
        differs from the local copy)."""
        cfg = _cfg()
        provider = _provider(cfg)
        resolved = provider.resolve(repo)
        files = provider.list_files(resolved)
        sizes: dict = {}
        try:
            sizes = provider.list_files_with_sizes(resolved) or {}
        except NotImplementedError:
            pass
        except Exception:
            sizes = {}
        cache_status = cache_status_for_repo(provider, resolved, cfg.registry.repo_root)
        return {"repo": resolved, "files": files, "sizes": sizes, "cacheStatus": cache_status}

    @mcp.tool(structured_output=True)
    def cache_list(category: str | None = None, query: str = "") -> CacheListResult:
        """Browse the locally cached models (scanned from the configured source
        repos). category: optional category filter; query: optional text filter on
        name/path. Returns {count, categories, models} with model cards (name,
        category, quant, size_gb, file_count, kind, files, source, path, key,
        display_name, upstream_repo, update_status, notes)."""
        from . import main as _app

        cfg = _cfg()
        models, _meta = _app.catalog(cfg)
        if category:
            models = [m for m in models if m.category == category]
        if query:
            q = query.lower()
            models = [m for m in models if q in m.name.lower() or q in m.path.lower()]
        models = sorted(models, key=lambda m: (m.category, m.name.lower()))
        return {
            "count": len(models),
            "categories": sorted({m.category for m in models}),
            "models": [_app.model_json(m) for m in models],
        }

    @mcp.tool(structured_output=True)
    def cache_status(repo: str) -> CacheStatusResult:
        """Per-file cache status for a hub repo: which of its files are cached and
        whether an update is available (local size differs from the hub's)."""
        cfg = _cfg()
        provider = _provider(cfg)
        resolved = provider.resolve(repo)
        return {"repo": resolved, "status": cache_status_for_repo(provider, resolved, cfg.registry.repo_root)}

    # -----------------------------------------------------------------------
    # Tools — jobs
    # -----------------------------------------------------------------------

    @mcp.tool(structured_output=True)
    def job_list() -> JobListResult:
        """List all background jobs (newest last), including status, progress, log
        lines and result. Use job_status(job_id) to poll a specific job."""
        return {"jobs": [j.to_dict() for j in queue.all()]}

    @mcp.tool(structured_output=True)
    def job_status(job_id: str) -> JobStatusResult:
        """Get the status of a background job by id. Poll after download_model /
        deploy_model / package_fetch until status is 'done' (success), 'failed'
        (check result.error and log) or 'cancelled'. Includes progress (0-100) and
        the accumulated log lines."""
        job = queue.get(job_id)
        if not job:
            raise ToolError(f"unknown job id {job_id!r}")
        return job.to_dict()

    @mcp.tool(structured_output=True)
    def job_cancel(job_id: str) -> JobActionResult:
        """Cancel a queued or running background job. Returns ok:true only if it was
        still cancellable (queued/running); a terminal job raises an error."""
        if not queue.cancel_job(job_id):
            raise ToolError(f"job {job_id!r} not found or already terminal")
        return {"ok": True}

    # -----------------------------------------------------------------------
    # Tools — download + deploy (submit-and-poll pattern)
    # -----------------------------------------------------------------------

    @mcp.tool(structured_output=True)
    def download_model(model: str, quants: List[str] | None = None,
                       no_mmproj: bool = False, mmproj: Optional[str] = None,
                       dry_run: bool = False) -> SubmitResult:
        """Download a model into the local cache (no target push). model: hub repo
        id or bare name. quants: quant strings (e.g. "q4_k_m") or exact
        repo-relative file paths to fetch; empty = no model file. By default an
        mmproj vision projector is auto-included; pass no_mmproj=true to skip it or
        mmproj=<remote file path> to pick a specific one. dry_run=true only reports
        what would be fetched. Returns {ok, job_id, mode:"cache"} — poll
        job_status(job_id) to completion."""
        cfg = _cfg()
        quants = quants or []
        job = queue.create("hf_download", f"HF download {model} [{', '.join(quants)}]")
        job.meta = {  # type: ignore[attr-defined]
            "model": model, "quants": quants, "output_dir": cfg.registry.repo_root,
            "token_env": cfg.registry.token_env, "dry_run": dry_run, "no_mmproj": no_mmproj,
            "mmproj": mmproj or None,
        }
        return {"ok": True, "job_id": job.id, "mode": "cache"}

    @mcp.tool(structured_output=True)
    def deploy_model(model: str, quants: List[str] | None = None,
                     target: Optional[str] = None, category: Optional[str] = None,
                     no_mmproj: bool = False, mmproj: Optional[str] = None,
                     dry_run: bool = False) -> SubmitResult:
        """Deploy a model to a target machine: download into the local cache, then
        push over rsync/SSH to the target's mapped directory. model: hub repo id or
        bare name. quants: quant strings or exact file paths. target: a target name
        from target_list(); if empty this only fills the cache (same as
        download_model). category: target path category (e.g. "llm", "image-gen");
        if omitted it is auto-inferred from the model name. Returns {ok, job_id,
        mode:"deploy"|"cache"} — poll job_status(job_id) until done/failed."""
        cfg = _cfg()
        quants = quants or []
        output_dir = cfg.registry.repo_root
        common = {
            "model": model, "quants": quants, "output_dir": output_dir,
            "token_env": cfg.registry.token_env, "dry_run": dry_run,
            "no_mmproj": no_mmproj, "mmproj": mmproj or None,
        }
        if not target:
            job = queue.create("hf_download", f"HF download {model} [{', '.join(quants)}]")
            job.meta = common  # type: ignore[attr-defined]
            return {"ok": True, "job_id": job.id, "mode": "cache"}

        tm = cfg.targets.get(target)
        if not tm:
            available = ", ".join(cfg.targets) or "(none configured)"
            raise ToolError(f"unknown target {target!r}; available: {available}")
        job = queue.create("deploy", f"Deploy {model} [{', '.join(quants)}] -> {target}")
        job.meta = {  # type: ignore[attr-defined]
            **common,
            "target": target, "category": category or "",
            "host": tm.host, "user": tm.user, "key": tm.ssh_key, "ntfs": tm.ntfs,
        }
        return {"ok": True, "job_id": job.id, "mode": "deploy"}

    # -----------------------------------------------------------------------
    # Tools — targets + disk
    # -----------------------------------------------------------------------

    @mcp.tool(structured_output=True)
    def target_list() -> TargetListResult:
        """List the configured target machines and their category -> remote path
        mappings. Use these names in deploy_model(target=...)."""
        cfg = _cfg()
        out = {}
        for name, t in cfg.targets.items():
            cats = {cat: {"remote_root": pm.remote_root, "subdirs": pm.subdirs}
                    for cat, pm in (t.categories or {}).items()}
            out[name] = {"host": t.host, "user": t.user, "ntfs": t.ntfs,
                         "ssh_key": t.ssh_key, "categories": cats}
        return {"targets": out}

    @mcp.tool(structured_output=True)
    def target_status() -> TargetStatusResult:
        """SSH connectivity snapshot for the configured targets, refreshed in the
        background every 15s: {target: {ok, detail, checked_at}}."""
        from . import main as _app

        snap = _app.conn_cache_snapshot()
        return {"targets": {k: {"ok": ok, "detail": d, "checked_at": t}
                            for k, (ok, d, t) in snap.items()}}

    @mcp.tool(structured_output=True)
    def disk_usage() -> DiskUsageResult:
        """Local + per-target disk usage gauges, refreshed in the background every
        30s: {"local": {total,used,free,pct}, "targets": {name: {filesystems}}}."""
        from . import main as _app

        return _app.disk_cache_snapshot()

    # -----------------------------------------------------------------------
    # Tools — packages
    # -----------------------------------------------------------------------

    @mcp.tool(structured_output=True)
    def package_list() -> PackageListResult:
        """List all custom model packages (named groups of files from one or more
        remote repos fetched together into the cache)."""
        return {"packages": packages.list_packages()}

    @mcp.tool(structured_output=True)
    def package_create(name: str, task: str = "", description: str = "",
                       components: List[dict] | None = None) -> PackageResult:
        """Create a custom model package. components: list of {"repo": "org/repo",
        "files": ["path/a.safetensors", ...], "label": "optional label"}. At least
        one component with at least one file is required; paths must be repo-relative
        (no .. or leading dots). Returns the created package; fetch it with
        package_fetch."""
        comps = components if components is not None else []
        try:
            pkg = packages.create_package(name, task, description, comps)
        except ValueError as e:
            raise ToolError(str(e))
        return {"ok": True, "package": pkg}

    @mcp.tool(structured_output=True)
    def package_update(package_id: str, name: str, task: str = "",
                       description: str = "", components: List[dict] | None = None) -> PackageResult:
        """Update an existing custom model package (rename, edit description or
        components). components follow the same shape as package_create; if omitted
        the package's current components are kept."""
        comps = components if components is not None else \
            (packages.get_package(package_id) or {}).get("components", [])
        try:
            pkg = packages.update_package(package_id, name, task, description, comps)
        except ValueError as e:
            raise ToolError(str(e))
        if pkg is None:
            raise ToolError(f"package {package_id!r} not found")
        return {"ok": True, "package": pkg}

    @mcp.tool(structured_output=True)
    def package_status(package_id: str) -> PackageStatusResult:
        """Per-file status of a package: which of its files are already cached,
        missing, or have an update available on the hub, with local/remote byte
        sizes."""
        pkg = packages.get_package(package_id)
        if pkg is None:
            raise ToolError(f"package {package_id!r} not found")
        cfg = _cfg()
        st = packages.status_for(pkg, cfg.registry.repo_root, _provider(cfg))
        return {"ok": True, **st}

    @mcp.tool(structured_output=True)
    def package_fetch(package_id: str, dry_run: bool = False) -> SubmitResult:
        """Download every missing/stale file of a package into the local cache.
        Runs as a background job. Returns {ok, job_id} — poll job_status(job_id)
        until done. dry_run=true only reports what would be fetched."""
        pkg = packages.get_package(package_id)
        if pkg is None:
            raise ToolError(f"package {package_id!r} not found")
        job = queue.create("package_fetch", f"Fetch package “{pkg['name']}”")
        job.meta = {"package_id": package_id, "dry_run": dry_run}  # type: ignore[attr-defined]
        return {"ok": True, "job_id": job.id, "mode": "cache"}

    @mcp.tool(structured_output=True)
    def package_delete(package_id: str) -> PackageDeleteResult:
        """Delete a custom model package (removes only the package definition, not
        any downloaded files on disk)."""
        removed = packages.delete_package(package_id)
        if not removed:
            raise ToolError(f"package {package_id!r} not found")
        return {"ok": True, "removed": package_id}