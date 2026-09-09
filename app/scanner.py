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

import json
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

# Co-located metadata file that marks a downloaded repo as one deployable group.
# Its presence forces every model file under the directory into a single tree
# card (see ``scan_source``) and carries the upstream tags to persist.
MANIFEST_NAME = ".modelmeta.json"


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
    """A directory is an HF/diffusers tree if it carries repo markers.

    Also detects pure-GGUF repos that mix main quants with auxiliary components
    (mmproj-*.gguf vision projectors, or subdirs like MTP/). These are always part
    of a single model, so the whole directory groups as one tree instead of each
    file becoming its own card. A bare "main file present" directory is still NOT
    a tree (independent flat quants stay separate) — the aux signal is required.
    """
    try:
        entries = os.listdir(dir_abs)
    except OSError:
        return False
    if any(m in entries for m in TREE_MARKERS):
        return True

    def _is_model_file(f: str) -> bool:
        return os.path.splitext(f)[1].lower() in MODEL_EXTS

    model_files = [f for f in entries if _is_model_file(f)]
    mmproj_files = [f for f in model_files if f.startswith("mmproj")]
    main_files = [f for f in model_files if not f.startswith("mmproj")]
    if mmproj_files and main_files:
        return True
    # Subdir with model files (e.g. MTP/) alongside a main file at this level.
    if main_files:
        for entry in entries:
            child = os.path.join(dir_abs, entry)
            if not os.path.isdir(child):
                continue
            try:
                child_files = os.listdir(child)
            except OSError:
                continue
            if any(_is_model_file(f) for f in child_files):
                return True
    return False


def _looks_like_shard_dir(dir_abs: str, model_exts: set[str]) -> bool:
    """A directory with multiple shard files (model-*-of-*.safetensors)."""
    try:
        shards = [f for f in os.listdir(dir_abs)
                  if re.search(r"model[-.]?\d*[-_]?of[-_]?\d", f, re.I)]
    except OSError:
        return False
    return len(shards) >= 2


def iter_model_files(root: str) -> list[str]:
    """Every model file under ``root`` (recursive), as repo-relative paths.

    Respects IGNORE_DIRS and MODEL_EXTS; returns [] when ``root`` isn't a dir.
    Exposed so manifest discovery shares one walk instead of re-scanning.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in MODEL_EXTS:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _find_manifests(root: str) -> dict[str, dict]:
    """Map each manifest-bearing directory to its parsed ``.modelmeta.json`` data.

    The source root itself is never treated as a group (its files stay
    independent). Entries are validated: the JSON must be an object carrying a
    non-empty ``repo_id``, otherwise it's skipped rather than raising.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return {}
    out: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        if MANIFEST_NAME in filenames:
            try:
                with open(os.path.join(dirpath, MANIFEST_NAME), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue  # unreadable / invalid manifest -> ignore
            repo_id = data.get("repo_id") if isinstance(data, dict) else None
            tags = data.get("tags", []) if isinstance(data, dict) else []
            if not repo_id:
                continue  # must name a repo to be a real group
            out[os.path.abspath(dirpath)] = {
                "repo_id": str(repo_id),
                "model_name": data.get("model_name"),
                "tags": tags if isinstance(tags, list) else [],
            }
    return out


def _manifest_owner(abs_path: str, manifests: dict[str, dict]) -> str | None:
    """Nearest manifest-bearing ancestor dir of ``abs_path`` (own dir included).

    Downloaded repos keep their upstream subpaths, so the co-located manifest must
    own every file beneath it — not just its immediate children.
    """
    d = os.path.dirname(abs_path)
    while True:
        if d in manifests:
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def scan_source(source_name: str, root: str) -> list[Model]:
    """Scan a single source repo and return grouped Model records."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return []

    manifests = _find_manifests(root)

    # Files under a manifest-bearing dir become one tree card named from the
    # manifest (subfolders included — HF repos keep their subpaths); everything
    # else keeps the existing natural grouping below.
    manifest_dirs: dict[str, list[tuple[str, str]]] = {}
    natural: dict[str, list[tuple[str, str]]] = {}
    for rel in iter_model_files(root):
        abs_path = os.path.join(root, rel.replace("/", os.sep))
        owner = _manifest_owner(abs_path, manifests)
        if owner is not None:
            manifest_dirs.setdefault(owner, []).append((abs_path, rel))
        else:
            key = _group_key(root, abs_path, rel)
            natural.setdefault(key, []).append((abs_path, rel))

    models: list[Model] = []

    # Manifest-bearing dirs -> one tree card each (kind forced to "tree" even for
    # a single file, since the files are grouped under the repo root).
    for abs_dir, members in manifest_dirs.items():
        members.sort(key=lambda x: x[1])
        total_size = sum(os.path.getsize(p) for p, _ in members)
        rel_paths = [r for _, r in members]
        if not rel_paths:
            continue
        rel_dir = os.path.relpath(abs_dir, root).replace(os.sep, "/")
        data = manifests[abs_dir]
        name = data["model_name"] or os.path.basename(rel_dir)
        models.append(Model(
            name=name,
            category=categorize(members[0][0]),
            quant=extract_quant(name),
            size_bytes=total_size,
            file_count=len(members),
            files=[r for _, r in members],
            kind="tree",
            source=source_name,
            path=rel_dir,
        ))

    # Everything else: existing grouping logic (unchanged).
    for key, members in natural.items():
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

    # Find the deepest ancestor directory that does NOT itself look like an HF
    # repo tree. A GGUF repo (e.g. unsloth/gemma-...-GGUF) can hold prior shards
    # inline plus subdirs like MTP/ that carry the aux weights; all of those must
    # collapse under the same repo root rather than becoming their own cards.
    abs_root = os.path.abspath(root)
    d = os.path.dirname(abs_path)
    tree_root = None
    while True:
        # The source root itself is never treated as a tree (its files stay
        # independent) — only subdirectories can be repo roots.
        if d != abs_root and (_looks_like_hf_repo(d) or _looks_like_shard_dir(d, MODEL_EXTS)):
            tree_root = d
            break
        parent = os.path.dirname(d)
        if parent == d or parent == abs_root or not parent.startswith(abs_root):
            break
        d = parent

    if tree_root is None:
        # No tree detected: a flat file in the repo root is its own model; a file
        # in a subdir (no markers) is also its own model.
        return rel_path

    rel_dir = os.path.relpath(tree_root, root).replace(os.sep, "/")
    return f"{rel_dir}::tree"


def _tree_display_name(members: list[tuple[str, str]]) -> str:
    """Pick a display name for a tree group."""
    rel_paths = [r for _, r in members]
    dirs = {os.path.dirname(r) for r in rel_paths}
    if len(dirs) == 1 and next(iter(dirs)):
        leaf_dir = next(iter(dirs))
        leaf = os.path.basename(leaf_dir)
        # A per-quant subdir inside a model package (e.g. "…-GGUF/UD-IQ2_XXS")
        # gains the package context so the card reads as a real name instead of
        # a bare quant ("UD-IQ2_XXS" -> "DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS").
        quant = extract_quant(leaf)
        if quant:
            residue = leaf.replace(quant, "", 1).strip("-_ .")
            if len(residue) <= 4:
                package = os.path.basename(os.path.dirname(leaf_dir))
                if package and package != ".":
                    return f"{package}/{leaf}"
        return leaf
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
