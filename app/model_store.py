"""Persistent, editable per-model metadata.

The catalog itself is derived from scanning the filesystem every request
(``scanner.scan_all``). This module adds a thin SQLite overlay so a model card
can carry user edited fields that survive a reload: a display-name override, a
category/quant override, an optional upstream source repo (for update checks),
free-form notes, a hidden flag, and the last known update-check result.

Models are keyed by ``"{source}::{repo-relative path}"`` — the two identity
fields the scanner already emits (``Model.source`` + ``Model.path``). This is
stable until the file is moved; a ``relink`` helper re-keys a row to a new path
when the user edits metadata after reorganising files.

Storage is stdlib ``sqlite3`` — no new dependency. The DB file path comes from
``MODEL_DB_PATH`` and defaults to ``<app dir>/models.db``.
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import threading
from pathlib import Path

_DB_PATH = Path(os.environ.get("MODEL_DB_PATH", str(Path(__file__).parent / "models.db")))

# A model card may carry these overlay fields. ``hidden`` and the update-check
# fields are internal; the rest are user-editable.
_EDITABLE = ("display_name", "category", "quant", "upstream_repo", "notes")
_INTERNAL = ("hidden", "update_status", "update_checked_at")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_meta (
    key              TEXT PRIMARY KEY,
    display_name     TEXT,
    category         TEXT,
    quant            TEXT,
    upstream_repo    TEXT,
    upstream_provider TEXT DEFAULT 'huggingface',
    hf_tags          TEXT,
    notes            TEXT,
    hidden           INTEGER DEFAULT 0,
    update_status    TEXT,
    update_checked_at TEXT,
    updated_at       TEXT
);
"""

_lock = threading.Lock()


def key_for(source: str, path: str) -> str:
    """Build a stable metadata key from the scanner's source name + rel path."""
    return f"{source}::{path}"


# Columns added after the initial release. ``CREATE TABLE IF NOT EXISTS`` never
# alters a pre-existing table, so old DB files (e.g. ones created before
# ``hf_tags`` existed) need an explicit ALTER on first connect.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("hf_tags", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(model_meta)")}
    for col, decl in _MIGRATIONS:
        if col not in have:
            conn.execute(f"ALTER TABLE model_meta ADD COLUMN {col} {decl}")
            conn.commit()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    _migrate(conn)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "key": row["key"],
        "display_name": row["display_name"],
        "category": row["category"],
        "quant": row["quant"],
        "upstream_repo": row["upstream_repo"],
        "upstream_provider": row["upstream_provider"],
        "hf_tags": row["hf_tags"],
        "notes": row["notes"],
        "hidden": bool(row["hidden"]),
        "update_status": row["update_status"],
        "update_checked_at": row["update_checked_at"],
        "updated_at": row["updated_at"],
    }


def get(key: str, provider: str | None = None) -> dict:
    """Return the metadata record for ``key``, or ``{}`` if none exists."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM model_meta WHERE key=?", (key,)).fetchone()
            return _row_to_dict(row) if row else {}
        finally:
            conn.close()


def get_or_default(key: str, provider: str = "huggingface") -> dict:
    """Return the record for ``key``, or an empty overlay dict for a fresh card."""
    data = get(key, provider)
    if not data:
        data = {"key": key, "upstream_provider": provider}
    return data


def list_all() -> dict[str, dict]:
    """Return every record keyed by its metadata key."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT * FROM model_meta").fetchall()
            return {r["key"]: _row_to_dict(r) for r in rows}
        finally:
            conn.close()


def save(key: str, fields: dict, provider: str = "huggingface") -> dict:
    """Upsert the editable fields for ``key``; returns the persisted record.

    Unknown keys in ``fields`` are ignored so callers can round-trip a card
    object safely. ``upstream_provider`` is fixed to the resolved provider.
    """
    import datetime as _dt

    existing = get(key, provider)
    merged = {**_EDITABLE_BASE(provider), **{k: fields.get(k) for k in _EDITABLE}}
    merged.update({k: fields.get(k) for k in _INTERNAL if k in fields})
    # Downloaded upstream tags are not user-editable; preserve them across edits so
    # a later metadata save can't clobber the tags recorded at download time.
    hf_tags = fields.get("hf_tags")
    if hf_tags is None and isinstance(existing, dict) and existing.get("hf_tags"):
        hf_tags = existing["hf_tags"]
    merged["hf_tags"] = hf_tags
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO model_meta
                   (key, display_name, category, quant, upstream_repo,
                    upstream_provider, hf_tags, notes, hidden, update_status,
                    update_checked_at, updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(key) DO UPDATE SET
                   display_name=excluded.display_name,
                   category=excluded.category,
                   quant=excluded.quant,
                   upstream_repo=excluded.upstream_repo,
                   upstream_provider=excluded.upstream_provider,
                   hf_tags=excluded.hf_tags,
                   notes=excluded.notes,
                    hidden=excluded.hidden,
                    update_status=excluded.update_status,
                    update_checked_at=excluded.update_checked_at,
                    updated_at=excluded.updated_at""",
                (
                    key,
                    merged["display_name"],
                    merged["category"],
                    merged["quant"],
                    merged["upstream_repo"],
                    provider,
                    merged["hf_tags"],
                    merged["notes"],
                    1 if merged["hidden"] else 0,
                    merged.get("update_status"),
                    merged.get("update_checked_at"),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM model_meta WHERE key=?", (key,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()


def _EDITABLE_BASE(provider: str) -> dict:
    return {"display_name": None, "category": None, "quant": None,
            "upstream_repo": None, "notes": None, "hidden": False,
            "update_status": None, "update_checked_at": None,
            "upstream_provider": provider}


def save_internal(key: str, fields: dict) -> dict:
    """Persist only backend-owned fields (update-status etc.), leaving the
    user-editable fields and ``updated_at`` untouched.

    Useful for the update check, which must not bump the "edited" marker or
    clobber a display-name the user has set. Upserts: if the row doesn't exist
    yet (e.g. an unattributed card that is checked after a seed/script wrote a
    plain upstream) it is created with the internal fields and empty edits.
    """
    if not fields:
        return get(key)
    allowed = tuple(k for k in fields if k in _INTERNAL)
    if not allowed:
        return get(key)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    set_fields = list(allowed) + ["update_checked_at"]
    vals = list(fields[k] for k in allowed) + [now, key]
    with _lock:
        conn = _connect()
        try:
            set_clause = ", ".join(f"{k}=?" for k in set_fields)
            cur = conn.execute(f"UPDATE model_meta SET {set_clause} WHERE key=?", vals)
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO model_meta (key, update_status, update_checked_at)"
                    " VALUES (?,?,?)",
                    (key, fields.get("update_status"),
                     fields.get("update_checked_at") or now))
            conn.commit()
            row = conn.execute("SELECT * FROM model_meta WHERE key=?", (key,)).fetchone()
            return _row_to_dict(row) if row else {}
        finally:
            conn.close()


def delete(key: str) -> bool:
    """Remove a metadata record; returns whether a row was actually deleted."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM model_meta WHERE key=?", (key,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def owner(key: str) -> tuple[str, str] | None:
    """Parse ``key`` back into ``(source, path)``; ``None`` when malformed."""
    if "::" not in key:
        return None
    source, _, path = key.partition("::")
    return (source, path) if source and path else None


def relink(old_key: str, new_key: str) -> bool:
    """Move a metadata row to a new key (e.g. after a file was relocated).

    Only re-keys when a row exists at ``old_key`` and nothing occupies
    ``new_key``, to avoid silently clobbering another card's edits.
    """
    with _lock:
        conn = _connect()
        try:
            exists_new = conn.execute(
                "SELECT 1 FROM model_meta WHERE key=?", (new_key,)).fetchone()
            if exists_new:
                return False
            cur = conn.execute(
                "UPDATE model_meta SET key=? WHERE key=? AND updated_at<=updated_at",
                (new_key, old_key))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Overlay: merge scanned Model records with persisted metadata
# ---------------------------------------------------------------------------

def apply_model_meta(models: list, meta: dict[str, dict] | None = None,
                     source_root: dict[str, str] | None = None):
    """Return ``(models, record_by_key)`` with metadata merged onto the scan.

    ``models`` is the list produced by ``scanner.scan_*``. Records with
    ``hidden`` set are dropped from the returned list. Each kept model gets a
    ``meta`` attribute (the effective record) so callers and templates can
    surface overrides and the upstream repo.

    ``source_root`` maps a source name to its root directory; used to resolve
    real file paths for update checks. Optional — when omitted, models that
    carry a ``source_root`` from the caller are used directly.
    """
    if meta is None:
        meta = list_all()
    kept: list = []
    for m in models:
        key = key_for(m.source, m.path)
        record = meta.get(key)
        if record:
            if record.get("hidden"):
                continue
            if record.get("display_name"):
                m.name = record["display_name"]
            if record.get("category"):
                m.category = record["category"]
            if record.get("quant"):
                m.quant = record["quant"]
            m.meta = record
            if source_root and m.source in source_root:
                m.source_root = source_root[m.source]
        else:
            m.meta = {"key": key, "upstream_provider": "huggingface"}
            if source_root and m.source in source_root:
                m.source_root = source_root[m.source]
        kept.append(m)
    return kept, meta
