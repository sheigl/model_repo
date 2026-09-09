"""Tests for the MCP HTTP mount (app/main.py _build_mcp + app/mcp_server.build_mcp_app).

Uses TestClient in a with-block so the FastAPI lifespan runs (which enters the MCP
session manager task group). Exercises the raw JSON-RPC handshake over /mcp and the
optional bearer-token auth middleware in isolation.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


def _initialize_request(session_id: str | None = None) -> dict:
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "mcp-mount-test", "version": "0.1.0"},
        },
    }
    if session_id:
        req["params"]["sessionId"] = session_id
    return req


class McpMountTests(unittest.TestCase):
    def test_mcp_round_trip(self):
        """Handshake + tools/list in a single lifespan (the MCP session manager
        can only be entered once per app instance)."""
        with TestClient(app) as client:
            resp = client.post("/mcp", json=_initialize_request())
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertIsNotNone(body.get("result"))
            self.assertEqual(body["result"]["serverInfo"]["name"], "model-repo")

            session_id = resp.headers.get("Mcp-Session-Id")
            self.assertTrue(session_id)
            list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            resp2 = client.post("/mcp", json=list_req, headers={"Mcp-Session-Id": session_id})
            self.assertEqual(resp2.status_code, 200)
            tools = resp2.json()["result"]["tools"]
            names = {t["name"] for t in tools}
            self.assertIn("hub_search", names)
            self.assertIn("download_model", names)
            self.assertIn("package_create", names)


class BearerAuthMiddlewareTests(unittest.TestCase):
    """Test the token gate that wraps the MCP app when mcp.auth_token is set."""

    @staticmethod
    def _make(token: str):
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        from app.mcp_auth import BearerAuthMiddleware

        async def inner(request):
            return JSONResponse({"ok": True})

        return BearerAuthMiddleware(Starlette(routes=[Route("/", inner, methods=["POST"])]), token)

    def test_missing_token_rejected(self):
        with TestClient(self._make("secret")) as client:
            resp = client.post("/", json={})
            self.assertEqual(resp.status_code, 401)
            self.assertIn("mcp auth", resp.json()["error"])

    def test_correct_token_accepted(self):
        with TestClient(self._make("secret")) as client:
            resp = client.post("/", headers={"Authorization": "Bearer secret"}, json={})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"ok": True})

    def test_wrong_token_rejected(self):
        with TestClient(self._make("secret")) as client:
            resp = client.post("/", headers={"Authorization": "Bearer wrong"}, json={})
            self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()