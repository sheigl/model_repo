# Architecture — Model Deployment Web App

## What the app is

A Python 3.11+ / FastAPI dashboard that treats a remote model hub (HuggingFace today,
pluggable tomorrow) as the **source of truth** and the server's local disk as a
**cache**. Users browse the hub, download models into the cache, and deploy them to
target machines over **rsync-over-SSH**. A single background worker thread executes
download/deploy/sync jobs so output stays ordered.

## Module map (`app/`)

| Module | Responsibility |
|--------|----------------|
| `main.py` | FastAPI app: all pages + `/api/*` routes, job `_runner`, background loops, app wiring. ~1292 lines. |
| `config.py` | `Config` / `Source` / `TargetMachine` / `PathMapping` / `RegistryConfig` dataclasses + YAML load/save. `CONFIG_PATH` from `MODEL_REPO_CONFIG`. |
| `registry.py` | `RepoProvider` abstraction (resolve / list_files / list_files_with_sizes / download / search / repo_meta), `HuggingFaceProvider`, `get_provider()`, `build_deploy_plan()`, `ensure_cached()`, `cache_status_for_repo()`. |
| `jobs.py` | `JobQueue` (single worker thread) + `Job` dataclass + `JobStatus` enum. Global singleton `queue`. Job kinds: `sync`, `hf_download`, `deploy`, `package_fetch`. |
| `scanner.py` | Filesystem catalog scanner → `Model` dataclasses (flat files + tree groups). `MANIFEST_NAME = ".modelmeta.json"`. |
| `model_store.py` | SQLite overlay for per-model metadata (display_name, category, quant, upstream_repo, notes, hidden, update status). Keyed `"{source}::{path}"`. |
| `packages.py` | Custom model packages (named groups of files from multiple repos), stored in SQLite `model_packages` table. `create/list/get/update/delete_package`, `status_for`, `fetch_package`. |
| `sync.py` | rsync-over-SSH engine: `run_rsync`, `build_rsync_command`, `test_connection`, `remote_disk_usage`, `local_disk_usage`. |
| `categorize.py` | `categorize(path)` → category string, `extract_quant`, `categorize_repo`. |
| `hf_download.py` | Thin compatibility shim over `registry` for the legacy `/api/hf/*` endpoints. |
| `templates/`, `static/` | Jinja2 pages (dark SaaS theme, Tailwind CDN) + JS/PWA assets. |

## Runtime state

- `queue` (jobs.py) — global `JobQueue`, worker started in `main._start_workers()`.
- `main._conn_cache` / `main._disk_cache` — background snapshots of SSH connectivity
  (every 15 s) and disk usage (every 30 s), guarded by `_conn_lock` / `_disk_lock`.
- `main._catalog(cfg)` — `scan_all(cfg.sources)` + `apply_model_meta(...)`.
- `main._model_json(m)` — serializes a scanned `Model` into the API card shape.

## Key flows

- **Deploy** (`POST /api/deploy`): resolve repo → `build_deploy_plan` → `ensure_cached`
  (phase 1, 0–60 % progress) → rsync each file to the target's category-mapped
  `remote_root` (phase 2, 60–100 %). No target = cache-only download.
- **Download** (`POST /api/hf/download`): same plan, cache only.
- **Sync** (`POST /api/sync`): push an existing source path to a target.
- **Package fetch** (`POST /api/packages/{id}/fetch`): download every file of every
  component repo into the cache.
- Jobs are created via `queue.create(kind, description)` + `job.meta = {...}`; the
  worker `_runner` dispatches on `job.kind`. UI polls `/api/jobs` or SSE `/api/stream/{id}`.

## Conventions

- Config is a hand-editable YAML (`app/config.yaml`), loaded fresh per request via
  `load_config(initialize=True)` (`_cfg()` in main.py).
- API responses are `{"ok": True, ...}` dicts; errors are `{"ok": False, "error": ...}`
  with 400/404/409 status codes.
- Tests: `unittest.TestCase` classes under `app/tests/`, run with pytest
  (`uv run pytest app/tests`). No network in tests — fake runners / patched subprocess.
- Entry point: `uv run model-repo` → `app.main:main` → uvicorn on `bind_host`/`app_port`.
  Runs as systemd user service `model-repo.service` (port 9999).