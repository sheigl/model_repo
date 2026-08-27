#!/usr/bin/env python3
"""Playwright e2e regression for the hub-browser Deploy flow (index.html drawer).

Catches client-side bugs that unit tests miss — e.g. the case where deploying from
the upstream hub got stuck at "enqueuing…" because `currentRepo` was never assigned,
so `drawerDeploy()` threw before ever calling fetch().

The app is booted in-process (uvicorn on an ephemeral port) and the two network
endpoints the drawer touches are intercepted, so the test is fully offline and
deterministic — no HuggingFace token or network required. Run it with:

    uv run --frozen python scripts/ui_test_deploy.py

It prints PASS/FAIL, writes a screenshot to /tmp/ui_deploy.png for visual inspection,
and exits non-zero on assertion failure (so it can gate CI).
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      "/home/openchamber/code/model_repo/.pwb")

from playwright.sync_api import sync_playwright  # noqa: E402
import uvicorn  # noqa: E402

HUB_REPO = os.environ.get("LIVE_REPO", "unsloth/Qwen3.8-27B-GGUF")
QUANT_VALUE = "Qwen_Qwen3.5-2B-Q4_K_M"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _boot_app() -> tuple[str, int]:
    """Start the FastAPI app on 127.0.0.1:<free port> in a background thread."""
    from app.main import app
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _serve():
        asyncio.run(server.serve())

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    for _ in range(300):  # wait up to ~3s for startup
        if getattr(server, "started", False):
            break
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("app did not start in time")
    return "127.0.0.1", port


def _load_env() -> None:
    """Load .env into os.environ so live tests have HF_TOKEN (not committed).

    Existing environment variables are never overwritten, so a token already set
    in the shell wins.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(os.path.abspath(env_path), override=False)


def main() -> int:
    _load_env()
    live = bool(os.environ.get("BASE_URL"))
    base_url = os.environ.get("BASE_URL") or f"http://{_boot_app()[0]}:{_boot_app()[1]}"
    if live:
        print(f"Live mode — testing {base_url} (real HuggingFace, no stubs)")
    else:
        host, port = _boot_app()
        base_url = f"http://{host}:{port}"
        print(f"Self-contained mode — booted app on {base_url}")

    deploy_bodies: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Bug was reported on an iPhone; exercise the mobile layout.
        context = browser.new_context(
            device_scale_factor=1,
            viewport={"width": 390, "height": 844},
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                        "Mobile/15E148 Safari/604.1"),
        )
        page = context.new_page()
        page.on("pageerror", lambda e: print("PAGEERROR:", e))

        if live:
            # Live mode: exercise the real HuggingFace flow (token from .env). Do NOT
            # click Deploy — that would start a real multi-GB download. Just confirm the
            # hub drawer lists real GGUF files, then screenshot for visual verification.
            page.goto(base_url, wait_until="domcontentloaded")
            page.evaluate("""(repo) => {
                window.currentHubModel = { id: repo, author: 'bartowski' };
                window.openHubRepo(repo);
            }""", HUB_REPO)
            page.wait_for_selector(".dw-quant", timeout=20000)
            file_count = page.locator(".dw-quant").count()
            title = page.locator("#dw-title").inner_text()
            screenshot_path = "/tmp/ui_deploy_live.png"
            page.screenshot(path=screenshot_path)
            context.close()
            browser.close()

            errors: list[str] = []
            if file_count == 0:
                errors.append("no real GGUF files listed for %s" % HUB_REPO)
            if title != HUB_REPO:
                errors.append(f"drawer title wrong: {title!r}")
            if errors:
                print("FAIL")
                for e in errors:
                    print("  -", e)
                return 1
            print(f"PASS: live hub drawer listed {file_count} real GGUF files for {title}.")
            print(f"screenshot: {screenshot_path}")
            return 0

        # --- self-contained mode: offline stubs + full regression flow ----------
        def _stub_hub_files(route):
            route.fulfill(
                status=200, content_type="application/json", json={
                    "repo": HUB_REPO,
                    "files": [f"{QUANT_VALUE}.gguf", "Qwen_Qwen3.5-2B-Q5_K_M.gguf"],
                })

        def _capture_deploy(route):
            req = route.request
            body = req.post_data
            if isinstance(body, bytes):
                body = body.decode("utf-8", "replace")
            deploy_bodies.append(body or "")
            route.fulfill(status=200, content_type="application/json", json={
                "ok": True, "job_id": "job-regression-1", "mode": "cache"})

        def _is(path):
            return lambda url: path in str(url).split("?", 1)[0]

        page.route(_is("/api/hub/files"), _stub_hub_files)
        page.route(_is("/api/deploy"), _capture_deploy)

        # --- drive the real UI: open a hub repo drawer, pick a quant, deploy ----
        page.goto(base_url, wait_until="domcontentloaded")
        page.evaluate("""(repo) => {
            window.currentHubModel = { id: repo, author: 'bartowski' };
            window.openHubRepo(repo);
        }""", HUB_REPO)

        # The drawer's file list is populated by the intercepted /api/hub/files call.
        page.wait_for_selector(".dw-quant", timeout=10000)
        page.check(".dw-quant[value='%s']" % QUANT_VALUE)
        page.click("#dw-deploy")

        # Success path: the result line flips to "queued" with a link to the job.
        page.wait_for_selector("#dw-result", timeout=10000)
        result_text = page.locator("#dw-result").inner_text()

        screenshot_path = "/tmp/ui_deploy.png"
        page.screenshot(path=screenshot_path)
        context.close()
        browser.close()

    # --- assertions (self-contained mode only) --------------------------------
    errors: list[str] = []
    if not deploy_bodies:
        errors.append("POST /api/deploy was never issued (JS threw before fetch)")
    else:
        body = deploy_bodies[-1]
        if "object object" in body.lower():
            errors.append(f"deploy sent an object as model, not the repo id: {body!r}")
        if HUB_REPO.replace("/", "%2F") not in body and HUB_REPO not in body:
            errors.append(f"deploy body missing the repo id: {body!r}")
    if "queued" not in result_text.lower():
        errors.append(f"result line did not show 'queued': {result_text!r}")

    if errors:
        print("FAIL")
        for e in errors:
            print("  -", e)
        return 1

    print("PASS: hub-browser Deploy issued POST /api/deploy and reported queued.")
    print(f"screenshot: {screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
