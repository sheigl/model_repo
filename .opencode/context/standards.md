# Coding Standards — Model Deployment Web App

## Language / runtime

- Python **>= 3.11**, managed with **uv** (`uv sync --frozen`, `uv run --frozen python …`).
- **Never invoke `.venv/bin/python` directly** — it may be a dead symlink after an
  external `uv run` (see AGENTS.md). Use `uv run --frozen python <script>`.
- To recover a clobbered venv: `uv venv --python /usr/bin/python3.13 && uv sync --frozen`.

## Dependencies

- Add deps to `pyproject.toml` only with justification; prefer stdlib.
- **MCP SDK**: `mcp>=2.0,<3` (v2 stable line). Import as `from mcp.server import MCPServer`
  (v2 renamed `FastMCP` → `MCPServer`; the `@mcp.tool()` decorator API is unchanged).
  Do NOT use v1 `FastMCP` / `mcp.server.fastmcp` imports.
- After changing deps: `uv sync --frozen` (watch for venv clobbering per AGENTS.md).

## Backend (FastAPI) conventions

- Config lives in `app/config.py` as dataclasses; add new sections as a new dataclass
  wired into `Config`, `_apply()`, `save()`, and `default_config()`. Old YAML files
  without the new section must keep working (`.get("section", {})` defaults).
- Business logic lives in modules (`registry`, `jobs`, `packages`, `scanner`, `sync`,
  `model_store`); `main.py` holds routes + the job `_runner` only.
- API responses: `{"ok": True, ...}` on success; `{"ok": False, "error": "..."}` with
  an appropriate 4xx status on expected failures. Never raise for expected failures.
- Jobs: create via `queue.create(kind, description)` then set `job.meta = {...}`.
  Job meta keys must match what `main._runner` reads for that kind.
- Config is read per request via `load_config(initialize=True)` (the `_cfg()` pattern).

## MCP server conventions (new)

- One `MCPServer` instance built by a factory `create_mcp_server(cfg)` in
  `app/mcp_server.py`; tools are plain functions decorated with `@mcp.tool()`.
- Tool names: `snake_case`, verb-first (`hub_search`, `download_model`, `job_status`).
- Tool return values: plain dicts (the SDK serializes them to JSON). For **expected**
  failures (unknown target, bad package id) raise `ToolError` from
  `mcp.server.mcpserver.exceptions` — the client sees `is_error=True` with a readable
  message. Never raise for unexpected errors; let them propagate (SDK reports them).
- **Async jobs**: tools that start downloads/deploys return immediately with
  `{"ok": True, "job_id": ...}`. The tool docstring MUST tell the agent to poll
  `job_status(job_id)` until `status` is `done`/`failed`/`cancelled`.
- Mounting: `mcp.streamable_http_app(streamable_http_path="/", transport_security=…,
  json_response=True)` returns a Starlette app; mount it with `app.mount(path, subapp)`.
  The host app's lifespan MUST enter `mcp.session_manager.run()` (via `AsyncExitStack`)
  or every request fails with "Task group is not initialized".
- `mcp.session_manager` exists only AFTER `streamable_http_app()` is called — build
  routes at module level, touch the manager only inside the lifespan.
- Transport security: pass `TransportSecuritySettings` explicitly. For LAN deployments
  behind a dynamic container IP use `enable_dns_rebinding_protection=False`; otherwise
  every request is rejected with `421` (the default allowlist is localhost-only).
- Optional auth: wrap ONLY the mounted MCP app in `BearerAuthMiddleware` (never the
  whole FastAPI app — the UI is unauthenticated by design). Use `hmac.compare_digest`.
- The MCP feature must be optional: guard the import/build in a try/except so a failure
  never prevents the main app from booting.

## Testing conventions

- `unittest.TestCase` classes under `app/tests/`, run with `uv run pytest app/tests`.
- No network: patch `subprocess.run`, fake providers, or use in-memory fakes.
- MCP tool tests: use the SDK's in-process client —
  `async with Client(create_mcp_server(cfg), raise_exceptions=True) as client:` then
  `await client.list_tools()` / `await client.call_tool(name, args)`. Check
  `result.is_error` and `result.structured_content`.
- Mount/auth tests: `fastapi.testclient.TestClient(app)` used as a context manager
  (`with TestClient(app) as c:`) so the lifespan (session manager) runs.
- Existing tests reference `main._refresh_disk()` / `main._disk_cache` — do not move
  or rename those without updating `app/tests/test_disk.py`.

## Deployment notes

- The app runs as systemd user service `model-repo.service` on port 9999. After code
  changes: `systemctl --user restart model-repo`. Logs: `journalctl --user -u model-repo`.
- Do not run a second instance alongside the service.