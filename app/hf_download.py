"""HuggingFace downloader — thin compatibility shim over `.registry`.

The provider-agnostic deploy logic lives in `.registry` (build_deploy_plan /
ensure_cached + RepoProvider). This module keeps the original HF-specific helper
names and `run_hf_download()` so the legacy `/api/hf/*` endpoints keep working,
implemented on top of ``HuggingFaceProvider``.
"""

from __future__ import annotations

import os

from .registry import (HuggingFaceProvider, build_deploy_plan, ensure_cached,
                       find_aux as find_mmproj, find_matches)

MMPROJ_PREFERRED = ["F16", "BF16", "F32"]


def resolve_repo(model: str) -> str:
    return HuggingFaceProvider().resolve(model)


def model_name_from_repo(repo_id: str) -> str:
    return HuggingFaceProvider().model_name(repo_id)


def list_repo_gguf_files(repo_id: str, token: str | None = None) -> list[str]:
    return HuggingFaceProvider(token).list_files(repo_id)


def run_hf_download(
    model: str,
    quants: list[str],
    output_dir: str,
    token_env: str = "HF_TOKEN",
    dry_run: bool = False,
    no_mmproj: bool = False,
    mmproj: str | None = None,
    on_step=None,
    check_staleness: bool = True,
    should_cancel=None,
    on_progress=None,
) -> dict:
    """Run an HF download job. Returns a summary dict; streams steps via callback."""
    provider = HuggingFaceProvider(token=os.environ.get(token_env))
    repo_id = provider.resolve(model)
    plan = build_deploy_plan(provider, model, quants, no_mmproj, mmproj)

    counts = {"downloaded": 0, "skipped": 0}

    def emit(ev):
        action = ev["action"]
        if action == "cached":
            counts["skipped"] += 1
        elif action == "download":
            counts["downloaded"] += 1
        if on_step:
            on_step({"action": action, "repo": repo_id,
                     "remote_file": ev.get("remote_file"), "local": ev.get("local"),
                     **({"error": ev["error"]} if "error" in ev else {})})

    if on_step:
        on_step({"action": "start", "repo": repo_id, "model": model, "quants": quants,
                 "output": output_dir, "dry_run": dry_run, "no_mmproj": no_mmproj,
                 "mmproj": mmproj})
    if not plan["plan"]:
        return {"ok": False, "repo": repo_id, "message": "No files matched your selection"}

    ensure_cached(provider, plan, output_dir, dry_run=dry_run, on_step=emit,
                  check_staleness=check_staleness, should_cancel=should_cancel,
                  on_progress=on_progress)

    return {"ok": True, "repo": repo_id, "downloaded": counts["downloaded"],
            "skipped": counts["skipped"], "dry_run": dry_run}
