# Playwright UI Testing — Standard

UI bugs that unit tests can't catch (e.g. a client-side JS error that throws *before*
the deploy fetch is ever issued, leaving the button stuck at "enqueuing…" and nothing
downloading) require a real browser. These are **Playwright** end-to-end tests: they drive
the actual rendered page and assert on DOM/network behavior.

This doc is the standard for writing, running, and visually verifying them.

## Prerequisites

- Browsers live at repo `.pwb` (persisted). Always export the path before any script:
  ```bash
  export PLAYWRIGHT_BROWSERS_PATH=/home/openchamber/code/model_repo/.pwb
  ```
- Run scripts with `uv run --frozen python <script>` — **never** `.venv/bin/python`
  directly (it can be a dead symlink after an external `uv` run; see AGENTS.md).
- `pytest` is intentionally **not** installed. Playwright checks are standalone runnable
  modules in `scripts/`, like the existing `unittest`-style tests — executed directly.

## Running a test

Each script prints `PASS` / `FAIL`, exits non-zero on assertion failure (so it can gate CI),
and writes a screenshot to `/tmp/<name>.png`.

```bash
# Self-contained mode (default): boots the app in-process on an ephemeral port, fully offline.
PLAYWRIGHT_BROWSERS_PATH=/home/openchamber/code/model_repo/.pwb \
  uv run --frozen python scripts/ui_test_deploy.py

# Live mode: point at a running server instead of booting it (real network / HF allowed).
# The script loads .env into the environment for HF_TOKEN, so live hub calls are authenticated.
BASE_URL=http://<container-ip>:9999 \
  PLAYWRIGHT_BROWSERS_PATH=/home/openchamber/code/model_repo/.pwb \
  uv run --frozen python scripts/ui_test_deploy.py

# Override which repo the live run opens (default: unsloth/Qwen3.8-27B-GGUF).
LIVE_REPO=unsloth/Qwen3-Flash-1B-GGUF BASE_URL=http://<container-ip>:9999 \
  PLAYWRIGHT_BROWSERS_PATH=/home/openchamber/code/model_repo/.pwb \
  uv run --frozen python scripts/ui_test_deploy.py
```

**Live tests are fine.** For manual/visual verification you can hit the real running app and
real endpoints (the upstream hub, etc.). Prefer offline/deterministic stubs for automated
guards; reach for live mode when you specifically want to exercise real network behavior.

> Live mode does **not** click Deploy — that would start a real multi-GB download. It opens a
> repo drawer and asserts the real HF file list populates, then screenshots. Use `LIVE_REPO`
> for a repo that actually exists (the default is confirmed-good). `.env` is loaded with
> `python-dotenv` without overwriting any `HF_TOKEN` already set in your shell.

## Visually inspecting screenshots

- **Vision is unreliable in this session** — image reads often fail with "Cannot read image".
  Do not rely on viewing a screenshot yourself. Instead: capture the PNG and attach/open it
  so the *user* can verify visually. Scripts already write to `/tmp/<name>.png`.
- To review, open the file in an image viewer or share the path (e.g. `/tmp/ui_deploy.png`).
- For regression baselines, committed reference shots live in `_shots/` (see AGENTS.md).

## Writing a new test script

Follow `scripts/ui_test_deploy.py` as the template:

1. **Boot offline.** Start the app with `uvicorn.Server` on a free port inside a thread
   (`asyncio.run(server.serve())`). Pick the port yourself via a bound socket so it doesn't
   depend on uvicorn internals. Support `BASE_URL` to switch to live mode.
2. **Stub network at the browser.** Use Playwright route interception for any endpoint that
   would need real network/auth — but match with a **function**, not a glob string:
   ```python
   # Plain strings are matched as URL *prefixes*, so "**/path" never matches.
   page.route(lambda url: "/api/hub/files" in str(url).split("?", 1)[0], handler)
   ```
   (The query string is part of the URL, so match on the path portion before `?`.)
3. **Drive the real UI** and assert on what a unit test can't see: that a request was issued,
   that DOM state changed, that no error was thrown. Assert on outcomes, not implementation.
4. **Use a realistic viewport.** Reproduce reported bugs (this one was on an iPhone) with
   `device_scale_factor` + a phone-sized viewport / device preset.
5. **Capture a screenshot** to `/tmp/…png` and exit non-zero on failure.

## Adding coverage for a discovered bug

When you fix a UI bug, add (or extend) a Playwright script that reproduces the exact failing
flow and asserts the fixed behavior — so it can't silently regress again before it reaches you.
