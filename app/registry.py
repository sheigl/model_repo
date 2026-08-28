"""Pluggable model-source registry.

The app treats a remote model hub (HuggingFace today, ModelScope tomorrow, etc.)
as the *source of truth* and the server's ``repo_root`` as a plain cache. A
``RepoProvider`` abstracts how to resolve a model id, list available files,
and download a single file into the local cache.

To add a new source, subclass ``RepoProvider`` and register it in
``PROVIDERS`` / ``get_provider()`` — nothing else in the app needs to change.
"""

from __future__ import annotations

import os
import shutil

from huggingface_hub import list_repo_files

# Auxiliary file hints (e.g. mmproj vision projectors) and their naming.
MMPROJ_PREFERRED = ["F16", "BF16", "F32"]

# Prefixes that never count as a quant match for the main model file.
_SKIP_PREFIXES = ("mmproj", "mtp-", "imatrix")


def find_matches(files: list[str], quants: list[str]) -> list[str]:
    """Find GGUF filenames matching the requested quantization strings."""
    matches = []
    for quant in quants:
        ql = quant.lower()
        found = None
        for f in files:
            fl = f.lower()
            if any(fl.startswith(s) for s in _SKIP_PREFIXES):
                continue
            if not fl.endswith(".gguf"):
                continue
            if fl.endswith(f"-{ql}.gguf"):
                found = f
                break
            if (f"{ql}." in fl or f"{ql}-" in fl) and not found:
                found = f
        if not found:
            for f in files:
                fl = f.lower()
                if any(fl.startswith(s) for s in _SKIP_PREFIXES):
                    continue
                if ql in fl and fl.endswith(".gguf"):
                    found = f
                    break
        matches.append(found)
    return matches


def find_aux(files: list[str], kind: str = "mmproj") -> str | None:
    """Find an auxiliary GGUF (e.g. mmproj) in the repo file list.

    Matches on basename so a mmproj living in a subfolder is still found; flat
    files are unaffected (their basename equals their path).
    """
    hints = {os.path.basename(k).lower(): k for k in files
             if os.path.basename(k).lower().startswith(kind) and k.lower().endswith(".gguf")}
    if not hints:
        return None
    for preferred in MMPROJ_PREFERRED:
        key = f"{kind}-{preferred.lower()}.gguf"
        if key in hints:
            return hints[key]
    return next(iter(hints.values()))


class RepoProvider:
    """Abstract model-source. Subclasses implement the four methods."""

    id: str = "generic"

    def resolve(self, model: str) -> str:
        """Normalize a user model spec into a repo id."""
        raise NotImplementedError

    def model_name(self, repo_id: str) -> str:
        """Human/display name for a repo id."""
        raise NotImplementedError

    def list_files(self, repo_id: str) -> list[str]:
        """List available model-granule filenames in a repo."""
        raise NotImplementedError

    def list_files_with_sizes(self, repo_id: str) -> dict[str, int | None]:
        """Map each GGUF filename to its remote size in bytes (best-effort).

        Optional capability; providers that can't report sizes leave it
        unimplemented and callers fall back to name-only listing.
        """
        raise NotImplementedError

    def download(self, repo_id: str, filename: str, local_dir: str) -> str:
        """Download one file into local_dir; return its local path."""
        raise NotImplementedError

    def search(self, query: str, *, limit: int = 12, offset: int = 0,
               gguf_only: bool = False) -> dict:
        """Search the upstream hub. Returns {"results": [...], "has_more": bool}.

        This is an optional capability; providers that can't browse simply leave
        it unimplemented (the UI falls back to file-listing per repo).
        """
        raise NotImplementedError


class HuggingFaceProvider(RepoProvider):
    """HuggingFace Hub implementation (default source)."""

    id = "huggingface"

    def __init__(self, token: str | None = None):
        self.token = token

    def resolve(self, model: str) -> str:
        if "/" in model:
            return model
        return f"unsloth/{model}-GGUF"

    def model_name(self, repo_id: str) -> str:
        name = repo_id.split("/")[-1]
        if name.lower().endswith("-gguf"):
            name = name[:-5]
        return name

    def list_files(self, repo_id: str) -> list[str]:
        try:
            files = list_repo_files(repo_id, token=self.token)
        except Exception as e:
            print(f"  !! could not list {repo_id}: {e}")
            return []
        return sorted(f for f in files if f.endswith(".gguf"))

    def list_files_with_sizes(self, repo_id: str) -> dict[str, int | None]:
        from huggingface_hub import HfApi

        api = HfApi(token=self.token)
        try:
            tree = api.list_repo_tree(repo_id, recursive=True, expand=True)
        except Exception as e:
            print(f"  !! could not list sizes for {repo_id}: {e}")
            return {}
        out: dict[str, int | None] = {}
        for item in tree:
            fn = getattr(item, "filename", None) or getattr(item, "path", None)
            if not fn or not fn.endswith(".gguf"):
                continue
            size = getattr(item, "size", None)
            out[fn] = int(size) if size is not None else None
        return {f: out[f] for f in self.list_files(repo_id)}

    def search(self, query: str, *, limit: int = 12, cursor: str | None = None,
               gguf_only: bool = False) -> dict:
        """Search the HF hub; returns repo cards for the UI.

        Pagination is best-effort: this library surfaces list_models as a flat
        generator, so we fetch one page (limit+1 to detect a next page) and expose
        ``has_more``. The user can refine the query or scroll within the page.
        """
        from huggingface_hub import HfApi

        api = HfApi(token=self.token)
        try:
            items = list(api.list_models(
                search=query, limit=limit + 1, filter="gguf" if gguf_only else None,
                full=True, sort="downloads" if not query else None,
            ))
        except Exception as e:
            print(f"  !! hub search failed for {query!r}: {e}")
            return {"results": [], "next_cursor": None, "has_more": False, "error": str(e)}

        has_more = len(items) > limit
        items = items[:limit]
        results = []
        for m in items:
            results.append({
                "id": m.id,
                "author": getattr(m, "author", None),
                "downloads": getattr(m, "downloads", 0) or 0,
                "likes": getattr(m, "likes", 0) or 0,
                "pipeline": getattr(m, "pipeline_tag", None),
                "tags": (getattr(m, "tags", None) or [])[:6],
                "last_modified": getattr(m, "lastModified", None),
            })
        return {"results": results, "next_cursor": has_more, "has_more": has_more}

    def download(self, repo_id: str, filename: str, local_dir: str) -> str:
        from huggingface_hub import hf_hub_download

        return hf_hub_download(
            repo_id=repo_id, filename=filename, local_dir=local_dir, token=self.token
        )


def get_provider(provider: str, token_env: str = "HF_TOKEN") -> RepoProvider:
    """Build a provider by name (config-driven, extensible)."""
    key = provider.strip().lower()
    if key in ("huggingface", "hf"):
        return HuggingFaceProvider(token=os.environ.get(token_env))
    raise ValueError(f"unknown model-source provider: {provider!r}")


# ---------------------------------------------------------------------------
# Deploy helpers (provider-agnostic)
# ---------------------------------------------------------------------------

def build_deploy_plan(provider: RepoProvider, model: str, quants: list[str],
                      no_mmproj: bool = False) -> dict:
    """Compute the exact files a deploy/download should operate on.

    Pure (only lists the repo); does not touch the local cache. Returns:
      {"repo_id", "model_name", "plan": [{"remote_file","local_name","kind"}]}
    """
    repo_id = provider.resolve(model)
    model_name = provider.model_name(repo_id)
    files = provider.list_files(repo_id)

    plan: list[dict] = []
    if not no_mmproj:
        mm = find_aux(files, "mmproj")
        if mm:
            prec = mm.rsplit(".", 1)[0].split("-", 1)[-1].upper()
            plan.append({"remote_file": mm, "local_name": f"mmproj-{prec}-{model_name}.gguf",
                         "kind": "mmproj"})
    for fname in find_matches(files, quants):
        if fname:
            # local_name is the HF-relative path so the cache and target mirror the
            # source layout (subfolders preserved).
            plan.append({"remote_file": fname, "local_name": fname,
                         "kind": "model"})

    # De-duplicate (two quant strings may match the same file).
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in plan:
        if item["remote_file"] in seen:
            continue
        seen.add(item["remote_file"])
        deduped.append(item)
    return {"repo_id": repo_id, "model_name": model_name, "plan": deduped}


def _remote_file_sizes(provider: RepoProvider, repo_id: str) -> dict[str, int | None]:
    """Best-effort ``{filename: size_bytes}`` for a repo.

    Returns an empty dict when the provider can't report sizes (method not
    implemented) or the call fails — callers then treat existing files as up to
    date and never re-download on that basis alone.
    """
    try:
        sizes = provider.list_files_with_sizes(repo_id)
    except NotImplementedError:
        return {}
    except Exception as e:
        print(f"  !! could not get remote sizes for {repo_id}: {e}")
        return {}
    return sizes or {}


def _download_and_place(provider: RepoProvider, repo_id: str, remote_file: str,
                        local_path: str, repo_root: str, local_name: str,
                        on_step=None) -> None:
    """Download one file into repo_root and relocate it to ``local_path``."""
    path = provider.download(repo_id, remote_file, repo_root)
    if os.path.normpath(path) != os.path.normpath(local_path):
        parent = os.path.dirname(local_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(path, local_path)
    if on_step:
        on_step({"action": "download", "remote_file": remote_file, "local": local_name})


def ensure_cached(provider: RepoProvider, plan: dict, repo_root: str,
                  dry_run: bool = False, on_step=None,
                  check_staleness: bool = True, force_refresh: bool = False,
                  should_cancel=None) -> list[str]:
    """Download any missing files from a build_deploy_plan() into repo_root.

    Files are stored mirroring the source layout: each item lands at
    ``repo_root/<local_name>`` where ``local_name`` is the HF-relative path for
    model files (subfolders preserved) or a friendly name for aux files such as
    mmproj.

    A file already on disk is normally reported as "cached" and skipped (the
    server-as-cache fast path). If ``check_staleness`` is set, its size is
    compared against the hub's copy: a mismatch means the file was re-released or
    corrected upstream, so it is overwritten rather than serving a stale cache.
    ``force_refresh`` always overwrites regardless of size. When remote sizes are
    unavailable the local file is left untouched (best-effort).

    Returns the local paths that should be present afterward, so the caller can
    deploy (sync) them to a target.
    """
    local_paths: list[str] = []
    remote_sizes = _remote_file_sizes(provider, plan["repo_id"]) if (check_staleness or force_refresh) else {}

    for item in plan["plan"]:
        if should_cancel and should_cancel():
            # Cancelled mid-transfer: stop before starting the next file.
            if on_step:
                on_step({"action": "cancelled", "remote_file": item["remote_file"], "local": item["local_name"]})
            break
        remote_file = item["remote_file"]
        local_name = item["local_name"]
        local_path = os.path.join(repo_root, local_name)
        remote_size = remote_sizes.get(remote_file)

        if os.path.exists(local_path):
            stale = False
            if force_refresh:
                stale = True
            elif check_staleness and remote_size is not None:
                try:
                    stale = os.path.getsize(local_path) != remote_size
                except OSError:
                    stale = True
            if stale:
                # Local copy differs from the hub (re-released / corrected file):
                # refresh it so we never download or sync a stale version.
                if dry_run:
                    if on_step:
                        on_step({"action": "dry-run", "remote_file": remote_file, "local": local_name})
                    local_paths.append(local_path)
                    continue
                try:
                    _download_and_place(provider, plan["repo_id"], remote_file, local_path,
                                        repo_root, local_name, on_step)
                except Exception as e:
                    if on_step:
                        on_step({"action": "download-error", "remote_file": remote_file, "error": str(e)})
                continue
            if on_step:
                on_step({"action": "cached", "remote_file": remote_file, "local": local_name})
            local_paths.append(local_path)
            continue

        if dry_run:
            if on_step:
                on_step({"action": "dry-run", "remote_file": remote_file, "local": local_name})
            continue
        try:
            _download_and_place(provider, plan["repo_id"], remote_file, local_path,
                                repo_root, local_name, on_step)
            local_paths.append(local_path)
        except Exception as e:
            if on_step:
                on_step({"action": "download-error", "remote_file": remote_file, "error": str(e)})
    return local_paths


def _file_status(cached: bool, size_remote: int | None, local_path: str) -> dict:
    """Build a per-file status record. ``up_to_date`` is only meaningful when the
    remote size is known; otherwise it stays False (can't tell)."""
    size_local = None
    up_to_date = False
    if cached and size_remote is not None:
        try:
            size_local = os.path.getsize(local_path)
            up_to_date = size_local == size_remote
        except OSError:
            pass
    return {"cached": cached, "up_to_date": up_to_date,
            "size_local": size_local, "size_remote": size_remote}


def check_cache_status(provider: RepoProvider, plan: dict, repo_root: str) -> list[dict]:
    """Per-plan-item cache status vs the hub, for UI badges ('update available')."""
    remote_sizes = _remote_file_sizes(provider, plan["repo_id"])
    out = []
    for item in plan["plan"]:
        rf = item["remote_file"]
        local_path = os.path.join(repo_root, item["local_name"])
        out.append({
            "remote_file": rf, "local_name": item["local_name"], "kind": item["kind"],
            **_file_status(os.path.exists(local_path), remote_sizes.get(rf), local_path),
        })
    return out


def cache_status_for_repo(provider: RepoProvider, repo_id: str, repo_root: str) -> dict[str, dict]:
    """Cache status for every GGUF file in a repo (repo-level; no quants needed).

    Maps each remote filename to {cached, up_to_date, size_local, size_remote},
    used by the UI to flag already-downloaded files that have an update available.
    """
    try:
        files = provider.list_files(repo_id)
    except Exception:
        files = []
    remote_sizes = _remote_file_sizes(provider, repo_id)
    out: dict[str, dict] = {}
    for f in files:
        local_path = os.path.join(repo_root, f)
        out[f] = _file_status(os.path.exists(local_path), remote_sizes.get(f), local_path)
    return out
