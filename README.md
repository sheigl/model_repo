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

mcp:                            # Model Context Protocol server (optional, on by default)
  enabled: true                 # false disables the /mcp endpoint
  path: /mcp                    # mount prefix (only /mcp is tested)
  auth_token: ""                # empty = open; set to require Bearer token
  allow_origins: []             # browser CORS origins (empty = none)
  trusted_hosts: []             # Host header allowlist (empty = any)

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

## MCP Server (AI agent access)

An optional [Model Context Protocol](https://modelcontextprotocol.io/) endpoint lets AI agents
inspect the cache, search the hub, submit download/deploy jobs and manage model packages over a
single `/mcp` transport — no browser, no HTML parsing required.

### Enable / disable

By default MCP is on. To disable or change settings, add or edit the `mcp:` section in
`config.yaml` (or via the **Settings** page):

```yaml
mcp:
  enabled: true            # set false to remove the /mcp mount
  path: /mcp
  auth_token: ""           # set a token to require Bearer auth
  allow_origins: []        # browser CORS origins for client-side apps
  trusted_hosts: []        # Host header allowlist (empty = any)
```

### Endpoint

`POST /mcp` — Streamable HTTP transport. Supports MCP protocol version 2025-03-26.
The session lifecycle is managed by the host app's lifespan, so the same endpoint handles
initialize → tools/call → session teardown.

When `auth_token` is set, every `POST /mcp` must carry `Authorization: Bearer <token>`.

### Available tools

| Tool | Purpose |
|------|---------|
| `hub_search` | Search the model hub for repos (text, pipeline filter, GGUF filter, pagination) |
| `hub_files` | List files in a hub repo with remote sizes and per-file cache status |
| `cache_list` | Browse the locally cached model catalog (filter by category/text) |
| `cache_status` | Per-file cache status for a specific hub repo |
| `download_model` | Download a model into the local cache (queued job) |
| `deploy_model` | Download + rsync to a named target (queued job) |
| `target_list` | List configured targets, their host/user/ntfs and category → path mappings |
| `target_status` | SSH connectivity snapshot per target (background refreshed every 15 s) |
| `disk_usage` | Local + per-target disk usage (background refreshed every 30 s) |
| `job_list` | List all background jobs with status, progress, log |
| `job_status` | Poll a specific job until `done` / `failed` / `cancelled` |
| `job_cancel` | Cancel a queued or running job |
| `package_list` | List custom model packages |
| `package_create` | Create a new package (named group of files from one or more repos) |
| `package_update` | Rename or edit a package's components |
| `package_status` | Per-file hub cache status for every file in a package |
| `package_fetch` | Download all missing/stale package files (queued job) |
| `package_delete` | Delete a package definition |

### Minimal client example

```python
import anyio
from mcp import Client
from mcp.shared.mcp_session import ClientSession

async def main():
    from app.mcp_server import create_mcp_server
    mcp = create_mcp_server()
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print("Tools:", [t.name for t in tools.tools])

        result = await client.call_tool("hub_search", {"query": "llama 3", "limit": 3})
        for repo in result.structured_content["results"]:
            print(f"  {repo['id']}  downloads={repo['downloads']}")

        # Submit a download job, then poll
        dl = await client.call_tool("download_model", {"model": "unsloth/gemma-3-4b-it-GGUF"})
        job_id = dl.structured_content["job_id"]
        print(f"Download job: {job_id}")

anyio.run(main)
```

Or from the CLI, using any MCP-compatible client:

```bash
# with the default config (no auth), POST directly to the endpoint:
curl -X POST http://localhost:9999/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-03-26","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0.1.0"}}}'
```

### Notes

- Every long-running tool (`download_model`, `deploy_model`, `package_fetch`) returns a
  `job_id` immediately. Agents must poll `job_status` until `status == "done"` or `"failed"`.
- The single background worker keeps downloads and rsync ordered exactly as the web UI does.
- All tools return `structured_content` (TypedDict JSON) alongside the human-readable JSON text.
- Setting `enabled: false` removes the mount entirely; a restart is required after toggling.
