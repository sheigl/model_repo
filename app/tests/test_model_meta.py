"""Tests for the per-model metadata store, its overlay onto the scan, the
quant-tree display-name fix, and the upstream update check.

The store is isolated to a temp DB by repointing ``model_store._DB_PATH``. The
upstream check uses a stub provider so no network or HF access is needed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import model_store
from app import main as main_app
from app.config import Config, Source
from app.scanner import Model, _tree_display_name

SOURCE = "my source"


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._old = model_store._DB_PATH
        model_store._DB_PATH = Path(tempfile.mkdtemp()) / "models.db"

    def tearDown(self):
        model_store._DB_PATH = self._old

    def test_save_get_delete_roundtrip(self):
        key = "s::model-Q4_K_M.gguf"
        rec = model_store.save(key, {
            "display_name": "My Model",
            "category": "llm",
            "quant": "Q4_K_M",
            "upstream_repo": "author/repo",
            "notes": "hello",
            "hidden": True,
        }, provider="huggingface")

        self.assertEqual(rec["display_name"], "My Model")
        self.assertEqual(rec["upstream_repo"], "author/repo")
        self.assertTrue(rec["hidden"])

        got = model_store.get(key)
        self.assertEqual(got["display_name"], "My Model")
        self.assertEqual(got["notes"], "hello")

        self.assertIn(key, model_store.list_all())
        self.assertTrue(model_store.delete(key))
        self.assertEqual(model_store.get(key), {})

    def test_save_migrates_preexisting_db_missing_hf_tags(self):
        # A DB created before ``hf_tags`` existed must be upgraded in place,
        # not fail on INSERT.
        import json
        import sqlite3

        db = model_store._DB_PATH
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE model_meta (
            key TEXT PRIMARY KEY, display_name TEXT, category TEXT, quant TEXT,
            upstream_repo TEXT, upstream_provider TEXT DEFAULT 'huggingface',
            notes TEXT, hidden INTEGER DEFAULT 0, update_status TEXT,
            update_checked_at TEXT, updated_at TEXT)""")
        conn.commit()
        conn.close()

        rec = model_store.save("s::m.gguf", {"hf_tags": '["llm"]'}, provider="huggingface")
        self.assertEqual(json.loads(rec["hf_tags"]), ["llm"])
        self.assertEqual(model_store.get("s::m.gguf")["hf_tags"], '["llm"]')

    def test_update_internal_preserves_editable_fields_and_updated_at(self):
        key = "s::m.gguf"
        model_store.save(key, {"display_name": "Keep Me", "upstream_repo": "a/b"},
                         provider="huggingface")
        saved = model_store.get(key)

        import datetime as _dt
        model_store.save_internal(key, {
            "update_status": "update-available",
            "update_checked_at": _dt.datetime(2026, 1, 1).isoformat(),
        })
        got = model_store.get(key)
        # Editable fields and the "edited" marker are untouched by a check.
        self.assertEqual(got["display_name"], "Keep Me")
        self.assertEqual(got["update_status"], "update-available")
        self.assertEqual(got["updated_at"], saved["updated_at"])

    def test_relink_moves_row(self):
        old_key = "s::old.gguf"
        new_key = "s::new.gguf"
        model_store.save(old_key, {"display_name": "Moved"}, provider="huggingface")
        self.assertTrue(model_store.relink(old_key, new_key))
        self.assertEqual(model_store.get(new_key)["display_name"], "Moved")
        self.assertEqual(model_store.get(old_key), {})

    def test_relink_refuses_to_clobber(self):
        a = "s::a.gguf"
        b = "s::b.gguf"
        model_store.save(a, {"display_name": "A"}, provider="huggingface")
        model_store.save(b, {"display_name": "B"}, provider="huggingface")
        self.assertFalse(model_store.relink(a, b))
        self.assertEqual(model_store.get(b)["display_name"], "B")


class OverlayTests(unittest.TestCase):
    def _model(self, name, path, source=SOURCE):
        return Model(name=name, category="llm", quant="", size_bytes=10,
                     file_count=1, files=[path], kind="file", source=source,
                     path=path)

    def test_hidden_model_is_dropped(self):
        key = model_store.key_for(SOURCE, "m.gguf")
        meta = {key: {"key": key, "hidden": True, "upstream_provider": "huggingface"}}
        models, _ = model_store.apply_model_meta(
            [self._model("m", "m.gguf")], meta=meta)
        self.assertEqual(len(models), 0)

    def test_overrides_and_attaches_meta(self):
        key = model_store.key_for(SOURCE, "m.gguf")
        meta = {key: {"key": key, "display_name": "Fancy Name", "category": "embedding",
                      "upstream_repo": "org/r", "upstream_provider": "huggingface",
                      "hidden": False}}
        models, _ = model_store.apply_model_meta(
            [self._model("m", "m.gguf")], meta=meta)
        self.assertEqual(models[0].name, "Fancy Name")
        self.assertEqual(models[0].category, "embedding")
        self.assertEqual(models[0].meta["upstream_repo"], "org/r")

    def test_unmatched_model_gets_default_meta(self):
        models, _ = model_store.apply_model_meta(
            [self._model("m", "m.gguf")], meta={})
        self.assertEqual(models[0].meta["key"], model_store.key_for(SOURCE, "m.gguf"))


class TreeNamingTests(unittest.TestCase):
    def test_quant_subdir_gains_package_context(self):
        members = [
            ("/cache/DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS/DS-00001-of-00003.gguf",
             "DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS/DS-00001-of-00003.gguf"),
            ("/cache/DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS/DS-00002-of-00003.gguf",
             "DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS/DS-00002-of-00003.gguf"),
            ("/cache/DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS/DS-00003-of-00003.gguf",
             "DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS/DS-00003-of-00003.gguf"),
        ]
        self.assertEqual(_tree_display_name(members),
                         "DeepSeek-V4-Flash-0731-GGUF/UD-IQ2_XXS")

    def test_plain_model_package_keeps_bare_name(self):
        members = [
            ("/cache/deepseek-ai/DeepSeek-V4-Flash-0731/model-00001-of-00002.safetensors",
             "deepseek-ai/DeepSeek-V4-Flash-0731/model-00001-of-00002.safetensors"),
            ("/cache/deepseek-ai/DeepSeek-V4-Flash-0731/model-00002-of-00002.safetensors",
             "deepseek-ai/DeepSeek-V4-Flash-0731/model-00002-of-00002.safetensors"),
        ]
        self.assertEqual(_tree_display_name(members), "DeepSeek-V4-Flash-0731")


class _StubProvider:
    id = "stub"

    def __init__(self, sizes):
        self._sizes = sizes

    def resolve(self, repo):
        return repo

    def list_files_with_sizes(self, repo):
        return dict(self._sizes)


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = model_store._DB_PATH
        model_store._DB_PATH = Path(tempfile.mkdtemp()) / "models.db"
        self.cfg = Config(sources=[Source(name=SOURCE, root=self.tmp)])

    def tearDown(self):
        model_store._DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, body):
        path = os.path.join(self.tmp, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
        return path

    def _check(self, repo, remote_size, local_body):
        key = model_store.key_for(SOURCE, "foo-Q4_K_M.gguf")
        self._write("foo-Q4_K_M.gguf", local_body)
        provider = _StubProvider({"foo-Q4_K_M.gguf": remote_size})
        with patch.object(main_app, "get_provider", return_value=provider):
            return main_app._check_update(self.cfg, key, {"upstream_repo": repo})

    def test_up_to_date_when_sizes_match(self):
        result = self._check("a/b", 6, b"123456")
        self.assertEqual(result["status"], "up-to-date")
        self.assertFalse(result["update_available"])

    def test_update_available_when_sizes_differ(self):
        result = self._check("a/b", 10, b"123456")
        self.assertEqual(result["status"], "update-available")
        self.assertTrue(result["update_available"])

    def test_unknown_when_remote_size_missing(self):
        result = self._check("a/b", None, b"123456")
        self.assertEqual(result["status"], "unknown")

    def test_persists_update_status(self):
        key = model_store.key_for(SOURCE, "foo-Q4_K_M.gguf")
        self._write("foo-Q4_K_M.gguf", b"123456")
        provider = _StubProvider({"foo-Q4_K_M.gguf": 10})
        with patch.object(main_app, "get_provider", return_value=provider):
            main_app._check_update(self.cfg, key, {"upstream_repo": "a/b"})
        self.assertEqual(model_store.get(key)["update_status"], "update-available")


if __name__ == "__main__":
    unittest.main()
