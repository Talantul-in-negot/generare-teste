"""ASGI request-body limits enforced before FastAPI parses JSON payloads."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Reject oversized fixed-length and chunked HTTP bodies.

    Validation on Pydantic fields is still useful, but it happens after the
    server has buffered and decoded the request. This boundary caps memory
    consumption before application parsing starts.
    """

    def __init__(self, app: ASGIApp, *, max_request_bytes: int) -> None:
        if max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be positive")
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_length = self._header(scope, b"content-length")
        if raw_length:
            try:
                content_length = int(raw_length)
                if content_length < 0:
                    raise ValueError
                if content_length > self.max_request_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                )(scope, receive, send)
                return

        body, disconnected = await self._read_bounded_body(receive)
        if disconnected:
            await JSONResponse({"detail": "Incomplete request body"}, status_code=400)(
                scope, receive, send
            )
            return
        if body is None:
            await self._reject(scope, receive, send)
            return

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _read_bounded_body(self, receive: Receive) -> tuple[bytes | None, bool]:
        parts: list[bytes] = []
        seen = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return b"", True
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            seen += len(chunk)
            if seen > self.max_request_bytes:
                return None, False
            if chunk:
                parts.append(chunk)
            if not message.get("more_body", False):
                return b"".join(parts), False

    @staticmethod
    def _header(scope: Scope, name: bytes) -> str:
        for key, value in scope.get("headers", []):
            if key.lower() == name:
                return value.decode("latin-1")
        return ""

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            {"detail": "Request body exceeds configured limit"}, status_code=413
        )(scope, receive, send)
