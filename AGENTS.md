# AGENTS.md — Model Deployment Web App

Operational notes for working on + testing this repo inside the OpenCode container.

## How to test (important)

- **The app is tested from OUTSIDE the OpenCode container** — via a browser hitting the
  container's IP and port (e.g. `http://<container-ip>:9999`), not by browsing files in-repo.
- **External testing clobbers the uv env.** Running `uv sync` / `uv run` from outside the
  container recreates `.venv` against a base Python that lives on the host but **not** inside
  this container: `.venv/bin/python` becomes a symlink to
  `/home/sheigl/miniconda3/bin/python3`, which is absent in-container. The venv launcher then
  breaks (`.venv/bin/python` exists as a name but won't execute → `No such file or directory`).

### Consequences for this agent

- **Do NOT invoke `.venv/bin/python` directly.** It may be a dead symlink after an external uv run.
- Prefer `uv run --frozen python <script>` — `uv` resolves its own interpreter and works even
  when the venv launcher is broken (e.g. running Playwright screenshot scripts).
- To recover a clobbered venv, recreate it against the system Python then freeze:
  ```bash
  uv venv --python /usr/bin/python3.13 && uv sync --frozen
  ```
- The service runs the entry point via `uv run model-repo` (uv manages its own
  interpreter), so a broken `.venv/bin/python` launcher does not stop the app — only direct
  `python` invocation and scripts that call it.

## Running the app

**The server runs as a systemd *user* service: `model-repo.service`** (user `sheigl`, uid 1000).
It is the thing that owns port 9999 — don't run a second instance alongside it.

- Unit file: `~/.config/systemd/user/model-repo.service` (enabled, `WantedBy=default.target`):
  - `ExecStart=%h/.local/bin/uv run model-repo`
  - `WorkingDirectory=/home/sheigl/code/model_repo`
  - `Environment=MODEL_REPO_CONFIG=/home/sheigl/code/model_repo/app/config.yaml`
  - `Type=simple`, `Restart=on-failure`, `RestartSec=5`
- Manage it:
  ```bash
  systemctl --user status  model-repo
  systemctl --user restart model-repo     # after code changes (uvicorn has no auto-reload)
  systemctl --user stop    model-repo     # only if you must run a manual instance
  journalctl --user -u model-repo -n 100 --no-pager   # logs (not /tmp/app_boot.log)
  ```
- **Killing the server PID makes systemd respawn it ~5 s later** (`Restart=on-failure`).
  Don't fight it with `/proc` PID scans for this app — restart via systemctl.
- Manual runs are only for when the service is stopped:
  ```bash
  # from /home/sheigl/code/model_repo
  uv sync --frozen                                  # deps + install this package (entry point: model-repo)
  setsid --fork uv run model-repo </dev/null >/tmp/app_boot.log 2>&1
  ```
  Note the unit uses plain `uv run` (no `--frozen`/`--no-sync`), so a service (re)start may
  sync the venv from the lockfile first — the same clobbering risk as external `uv run`s
  (see "How to test" above).
- Config: `app/config.yaml` (`bind_host`, `app_port`, `sources`, `targets`, `registry`).
  Default port is `9999`. Override with `MODEL_REPO_CONFIG`.
- Single entry point: `uv run model-repo` → launches uvicorn on config's `bind_host`/`app_port`.
- For other (non-model-repo) stray processes: no `pkill`/`pgrep`/`fuser`; scan
  `/proc/*/cmdline` for the PID and kill it directly.

## Playwright / screenshots (for verifying UI)

- Browsers are persisted at repo `.pwb`; export `PLAYWRIGHT_BROWSERS_PATH=/home/openchamber/code/model_repo/.pwb`.
- Run scripts with `uv run --frozen python <script>` (see "Consequences" above).
- **Vision is unreliable in this session** — image reads often fail with "Cannot read image".
  Don't rely on viewing screenshots yourself; capture them to `/tmp/` and let the user verify.

## Layout / design system

- `app/templates/base.html` holds the dark-SaaS theme (Tailwind via CDN). Responsive parts use
  Tailwind utilities (`md:flex-col`, `w-full md:w-auto`, `min-h-[44px]`) except:
  - nav horizontal-scroll + snap (`tab-row`), and **hiding its scrollbar** — needs raw CSS.
  - iOS input-zoom fix (`font-size:16px`) — Tailwind has no max-* responsive variant.
- Keep scrollbars visible (`bg-slate-500/70` thumb, not `ink-600`) and keep secondary text bright
  enough to read on phones (`.text-slate-400`/`.text-slate-500` are lifted under `max-width:767px`).
