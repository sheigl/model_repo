"""Optional bearer-token auth for the MCP endpoint only.

The web app's UI/API are unauthenticated (LAN tool); when ``mcp.auth_token`` is
set in config, this middleware wraps *only* the mounted MCP sub-app so AI agents
must present ``Authorization: Bearer <token>``. The rest of the app stays open.
"""

from __future__ import annotations

import hmac

from starlette.responses import JSONResponse


class BearerAuthMiddleware:
    """Reject requests without a valid ``Authorization: Bearer <token>`` header."""

    def __init__(self, app, token: str):
        self.app = app
        self._expected = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"")
        if not auth.startswith(b"Bearer "):
            await self._reject(scope, receive, send, "missing bearer token")
            return
        provided = auth[len(b"Bearer "):].strip()
        if not hmac.compare_digest(provided, self._expected):
            await self._reject(scope, receive, send, "invalid bearer token")
            return
        await self.app(scope, receive, send)

    async def _reject(self, scope, receive, send, detail: str):
        response = JSONResponse({"error": f"mcp auth: {detail}"}, status_code=401)
        await response(scope, receive, send)