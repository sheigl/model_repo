"""Seed the per-model metadata store with upstream HuggingFace repos.

The catalog is scanned from disk and has no idea where each file came from, so
pre-existing models can't be update-checked. This script takes the best-effort
attribution already recorded in ``scripts/migrate_known_repos.json`` (machine
search pass 1) and writes ``upstream_repo`` onto matching model cards so the
"Check for updates" button works out of the box. Everything it writes remains
editable from the app's Metadata tab.

The JSON is keyed by the *flat cache filename*; we match against each scanned
model by file basename (case-insensitive), so nested trees and flat files both
work. Models with no confident candidate are left unattributed.

Usage:
    uv run --frozen python scripts/seed_model_meta.py            # dry run (list only)
    uv run --frozen python scripts/seed_model_meta.py --apply    # write the store
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import model_store
from app.config import load_config
from app.scanner import scan_all  # noqa: E402


def _load_attribution() -> dict[str, str]:
    """Map a lowercased filename to its best-match upstream repo.

    Source order: ``attribution`` (HF-verified basename) first; then
    ``manual_review`` candidates, preferring a candidate whose repo name ends
    with the file's quant string, then falling back to the first candidate.
    """
    path = PROJECT_ROOT / "scripts" / "migrate_known_repos.json"
    if not path.exists():
        print(f"  !! {path} missing; nothing to seed")
        return {}
    data = json.loads(path.read_text())

    by_file: dict[str, str] = {}

    for fname, repo in (data.get("attribution") or {}).items():
        if repo:
            by_file.setdefault(fname.strip().lower(), repo)

    for entry in data.get("manual_review") or []:
        fname = entry.get("file")
        candidates = entry.get("candidates") or []
        if not fname or not candidates:
            continue
        quant = _quant_of(fname)
        best = None
        if quant:
            for cand in candidates:
                base = cand.rsplit("/", 1)[-1].lower()
                if quant.lower() in base:
                    best = cand
                    break
        if best is None:
            best = candidates[0]
        by_file.setdefault(fname.strip().lower(), best)

    return by_file


def _quant_of(file: str) -> str:
    """Cheap quant guess for candidate ranking (mirror of categorize.QUANTS)."""
    from app.categorize import QUANTS
    low = file.lower()
    for q in sorted(QUANTS, key=len, reverse=True):
        if q.lower() in low:
            return q
    return ""


def _basenames(model) -> list[str]:
    return [os.path.basename(f).lower() for f in model.files]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write metadata to the store (default is dry-run)")
    ap.add_argument("--db", default=None,
                    help="override the metadata DB path (default: MODEL_DB_PATH/env)")
    args = ap.parse_args()

    if args.db:
        os.environ["MODEL_DB_PATH"] = args.db

    cfg = load_config()
    by_file = _load_attribution()
    if not by_file:
        print("no attribution data loaded; nothing to seed")
        return 0

    models = scan_all(cfg.sources)
    applied = skipped = 0
    for m in models:
        key = model_store.key_for(m.source, m.path)
        existing = model_store.get(key)
        if existing.get("upstream_repo"):
            skipped += 1  # user (or a prior seed) already set it
            continue
        repo = None
        for base in _basenames(m):
            if base in by_file:
                repo = by_file[base]
                break
        if not repo:
            continue
        if args.apply:
            model_store.save(key, {"upstream_repo": repo}, provider=cfg.registry.provider)
        print(f"  {('set   ' if args.apply else 'would ')}{m.name!r} -> {repo}")
        applied += 1

    print(f"\nattributed {applied} model card(s); {skipped} already had an upstream repo")
    if not args.apply:
        print("dry run — rerun with --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
