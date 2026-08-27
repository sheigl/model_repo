# Model Deployment Web App

A small Python/FastAPI dashboard that treats a remote **model hub as the source of truth**
(HuggingFace today; ModelScope, etc. pluggable tomorrow) and the server as a transparent
**cache**, letting you pick a model/quant/mmproj and **deploy** it to a target machine:
anything already cached is pushed directly; anything missing is downloaded to the cache
first, then pushed — over **rsync-over-SSH**.

**Stack:** FastAPI + HTMX + Jinja2 + Tailwind (CDN). Binds `0.0.0.0:8321`.

## Routes

| Path | Purpose |
|------|---------|
| `/` | Inventory catalog (filterable by source / category / quant) |
| `/targets` | Target-machine cards, connectivity status, per-category sync form |
| `/downloads` | **Deploy** — pick a model from the source hub, choose a target, deploy |
| `/jobs` | Live job queue with SSE progress streaming |
| `/settings` | Edit `config.yaml` (sources / targets / registry) in a textarea |

API helpers used by the UI: `/api/inventory`, `/api/deploy` (POST), `/api/sync` (POST),
`/api/hf/list?repo=…`, `/api/hf/download` (POST, cache-only), `/api/jobs`, `/api/job/{id}`,
`/api/targets/status`, `/api/stream/{id}` (SSE).

## Configuration

Settings live in a single hand-editable YAML file. The app auto-generates a default
`config.yaml` on first run if none exists; edit it from the **Settings** page or directly on
disk. Point the app at a specific file with `MODEL_REPO_CONFIG`:

```bash
MODEL_REPO_CONFIG=/path/to/config.yaml uv run uvicorn app.main:app --port 8321
```

### Schema

```yaml
sources:                       # source-of-truth repos cataloged on THIS server
  - name: "GGUF / LLM models"  # kind: "flat" (GGUF) or "comfyui"
    root: "/mnt/4TB/AI/models"
    kind: flat

targets:                        # remote machines synced to over rsync-over-SSH
  ai.home:                      # target name (referenced by the UI)
    host: ai.home               # IP / Tailscale MagicDNS name reachable from here
    user: sheigl                # SSH user on the target
    ssh_key: null               # path to a private key, or null -> ~/.ssh/id_ed25519
    ntfs: true                  # set if the target data drive is NTFS (drops -goP)
    categories:                 # category -> where it lands on the target
      llm:
        remote_root: /mnt/1TB/AI/chat_models
      image-gen:
        remote_root: /mnt/1TB/AI/ComfyUI/ComfyUI/models/checkpoints

registry:                       # source of truth (pluggable model hub)
  enabled: true
  provider: huggingface         # "huggingface" | "modelscope" | ... (add in app/registry.py)
  token_env: HF_TOKEN           # env var holding the auth token (if any)
  repo_root: "/mnt/4TB/AI/models"   # server-side cache dir
  options: {}                   # provider-specific options (future)

app_port: 8321
bind_host: "0.0.0.0"
```

### Environment variables

| Var | Meaning |
|-----|---------|
| `MODEL_REPO_CONFIG` | Path to the config file (default: next to `app/`, i.e. `app/config.yaml`) |
| `HF_TOKEN` | Provider token, read from the env named in `registry.token_env` |

## How deploy / sync / downloads work

Jobs run on a **single background worker thread** so rsync/download output stays ordered. A job
is created (queued), then processed to `running` → `done` or `failed`. The UI polls `/api/jobs`
and/or subscribes to the SSE stream `/api/stream/{id}` for live log lines and progress.

- **Deploy** (`/api/deploy`) is the primary flow. It resolves the model on the chosen
  provider, computes the exact files (matched quant(s) + optional mmproj), then:
  1. **ensure-cached** — any file not already on disk is downloaded into `registry.repo_root`
     (the server-as-cache fast path);
  2. **sync** — every cached file is pushed to the target's mapped `remote_root` for the
     model's category (inferred, or overridden in the form) via `rsync -e "ssh -i <key>"`.
  Leaving the target blank is "cache only" — just downloads into the repo. Pass
  `dry_run: true` to validate the plan without transferring anything. Set `ntfs: true` on
  NTFS targets so the client drops the `-goP` flags.
- **Sync** (`/api/sync`) pushes an existing source path (`Source.root + local_path`) to a
  target's mapped remote root.
- **Provider** is pluggable via `app/registry.py`: implement a `RepoProvider` (resolve /
  list_files / download), register it, and select it with `registry.provider`. Adding
  ModelScope or any other hub requires no changes to the deploy/sync job code.

## Run locally

```bash
uv sync                                   # creates .venv + installs this package
MODEL_REPO_CONFIG=/tmp/test_config.yaml uv run model-repo   # start the app
# or just: uv run model-repo              # binds 0.0.0.0:8321 by default
```

`uv run model-repo` is the single entry point — it launches uvicorn on
`bind_host`/`app_port` from `config.yaml`. Open http://localhost:8321 .

## Deploy with Docker

Mirrors the `home_dashboard` pattern (`network_mode:host`, watchtower auto-update label).

```bash
docker compose build
docker compose up -d
```

The container exposes port 8321 on the host network and mounts `./config.yaml`. For **real**
syncing/downloads you must also make the source data and SSH credentials reachable inside the
container, e.g.:

```yaml
    volumes:
      - ./config.yaml:/app/config.yaml
      - /mnt/4TB/AI/models:/mnt/4TB/AI/models:ro   # source repos (host paths)
      - ~/.ssh:/root/.ssh:ro                       # SSH key for rsync-over-SSH
```

The image installs `rsync` and `openssh-client` since they are required at runtime.

## Notes & limitations

- Source roots (`Source.root`) are local paths on the server running the app; they must exist
  and be readable there (mounted in, if containerized). Targets are reached over SSH.
- Connectivity checks run every 15s in the background and cache results per target.
- The default config ships two example targets (`ai.home`, `desktop2.home`); edit or replace
  them to match your environment.
