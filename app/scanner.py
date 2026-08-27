"""Catalog scanner: walk a source repo and produce an inventory of model files.

A "model" here is either:
  - a single file (e.g. a flat .gguf), or
  - a tree / shard group (multi-file models like HF shards, diffusers pipelines,
    split_files/, per-quant subdirs). Trees are grouped so the whole thing syncs
    as one unit and rsync preserves its structure verbatim.

Grouping rules:
  - HF-style shards (model.safetensors-00001-of-00002.safetensors, or a dir with
    model_index.json / tokenizer.json alongside weights) -> grouped by the shard
    stem within its directory.
  - ComfyUI split_files/<cat>/... -> grouped under the parent category dir.
  - Per-quant subdirs (DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS/) -> each subdir is
    its own tree keyed by the subdir name.
  - Flat GGUF dirs where every file is an independent quant (gemma-4/, etc.) ->
    each file is its own model.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .categorize import categorize, extract_quant

MODEL_EXTS = {".gguf", ".safetensors", ".bin", ".pt", ".pth"}

# Directories never scanned: source, build, cache, venvs, ollama blob stores.
IGNORE_DIRS = {
    ".git", ".github", ".cache", "__pycache__", "node_modules",
    ".venv", "venv", "build", "dist", ".tox", ".mypy_cache", ".pytest_cache",
    "site-packages", "blobs", "manifests",
}

# Markers that indicate a directory is an HF / diffusers repo (a tree).
TREE_MARKERS = {".gitattributes", "model_index.json", "tokenizer.json",
                "config.json", "generation_config.json"}


@dataclass
class Model:
    name: str            # display name / group key
    category: str
    quant: str
    size_bytes: int
    file_count: int
    files: list[str]     # repo-relative paths (sorted)
    kind: str            # "file" or "tree"
    source: str          # source repo name
    path: str            # repo-relative path of the group root

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024 ** 3), 3)


def _is_hf_shard(rel_path: str) -> bool:
    """True if this file is part of an HF shard group."""
    stem = os.path.splitext(rel_path)[0]
    return bool(re.search(r"model[-.]?\d*[-_]?of[-_]?\d", rel_path, re.I)) or \
        "-of-" in rel_path.lower()


def _looks_like_hf_repo(dir_abs: str) -> bool:
    """A directory is an HF/diffusers tree if it carries repo markers."""
    try:
        entries = os.listdir(dir_abs)
    except OSError:
        return False
    return any(m in entries for m in TREE_MARKERS)


def _looks_like_shard_dir(dir_abs: str, model_exts: set[str]) -> bool:
    """A directory with multiple shard files (model-*-of-*.safetensors)."""
    try:
        shards = [f for f in os.listdir(dir_abs)
                  if re.search(r"model[-.]?\d*[-_]?of[-_]?\d", f, re.I)]
    except OSError:
        return False
    return len(shards) >= 2


def scan_source(source_name: str, root: str) -> list[Model]:
    """Scan a single source repo and return grouped Model records."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return []

    # Collect all model files first.
    collected: list[tuple[str, str]] = []  # (abs_path, rel_path)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in MODEL_EXTS:
                continue
            abs_path = os.path.join(dirpath, fn)
            rel_path = os.path.relpath(abs_path, root)
            collected.append((abs_path, rel_path))

    # Group into trees.
    groups: dict[str, list[tuple[str, str]]] = {}
    for abs_path, rel_path in collected:
        key = _group_key(root, abs_path, rel_path)
        groups.setdefault(key, []).append((abs_path, rel_path))

    models: list[Model] = []
    for key, members in groups.items():
        members.sort(key=lambda x: x[1])
        total_size = sum(os.path.getsize(p) for p, _ in members)
        rel_paths = [r for _, r in members]
        if len(rel_paths) == 1:
            display_name = os.path.basename(rel_paths[0])
            kind = "file"
        else:
            display_name = _tree_display_name(members)
            kind = "tree"
        category = categorize(os.path.join(root, members[0][1]))
        quant = extract_quant(display_name)
        models.append(Model(
            name=display_name,
            category=category,
            quant=quant,
            size_bytes=total_size,
            file_count=len(members),
            files=[r for _, r in members],
            kind=kind,
            source=source_name,
            path=os.path.dirname(rel_paths[0]) if kind == "tree" else rel_paths[0],
        ))

    models.sort(key=lambda m: (m.category, m.name.lower()))
    return models


def _group_key(root: str, abs_path: str, rel_path: str) -> str:
    """Compute the group key for a file (tree grouping)."""
    parent = os.path.dirname(rel_path)

    # HF-style shard files -> grouped by normalized stem within their dir.
    if _is_hf_shard(rel_path):
        stem = re.sub(r"-\d+[-_]of[-_]\d+", "", os.path.splitext(rel_path)[0])
        return f"{parent}::{stem}"

    # ComfyUI split_files/<cat>/... -> grouped under the parent category dir.
    parts = rel_path.split("/")
    if "split_files" in parts:
        idx = parts.index("split_files")
        base = "/".join(parts[:idx + 1])
        return f"{base}::split"

    # Per-quant subdirs / diffusers pipelines: group by containing directory,
    # but only if that dir actually looks like a multi-file tree.
    parent_abs = os.path.dirname(abs_path)
    is_tree = _looks_like_hf_repo(parent_abs) or _looks_like_shard_dir(parent_abs, MODEL_EXTS)

    # A flat GGUF file in the repo root (parent == ".") is always its own model.
    if parent == "." and not is_tree:
        return rel_path

    if is_tree:
        return f"{parent}::tree"
    # Not a tree -> each file is its own model, even inside a subdir.
    return rel_path


def _tree_display_name(members: list[tuple[str, str]]) -> str:
    """Pick a display name for a tree group."""
    rel_paths = [r for _, r in members]
    dirs = {os.path.dirname(r) for r in rel_paths}
    if len(dirs) == 1 and next(iter(dirs)):
        return os.path.basename(next(iter(dirs)))
    # Fall back to the first file's stem (strip shard suffix).
    stem = os.path.splitext(rel_paths[0])[0]
    stem = re.sub(r"-\d+[-_]of[-_]\d+$", "", stem)
    return stem


def scan_all(sources: list) -> list[Model]:
    """Scan all configured sources and merge into one inventory."""
    models: list[Model] = []
    for src in sources:
        try:
            models.extend(scan_source(src.name, src.root))
        except OSError as e:
            print(f"  !! could not scan {src.name} ({src.root}): {e}")
    return models
