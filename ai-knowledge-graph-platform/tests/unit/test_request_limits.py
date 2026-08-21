from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.request_limits import RequestBodyLimitMiddleware


async def _echo_size(request: Request) -> JSONResponse:
    return JSONResponse({"size": len(await request.body())})


def _app(limit: int = 8) -> RequestBodyLimitMiddleware:
    return RequestBodyLimitMiddleware(
        Starlette(routes=[Route("/echo", _echo_size, methods=["POST"])]),
        max_request_bytes=limit,
    )


async def test_fixed_length_body_is_rejected_before_dispatch() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post("/echo", content=b"123456789")

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body exceeds configured limit"}


async def test_chunked_body_is_bounded_and_valid_body_is_replayed() -> None:
    async def oversized_chunks():
        yield b"12345"
        yield b"6789"

    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post("/echo", content=oversized_chunks())
        accepted = await client.post("/echo", content=b"12345678")

    assert rejected.status_code == 413
    assert accepted.json() == {"size": 8}


async def test_disconnect_before_complete_body_is_rejected() -> None:
    messages = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    sent: list[dict] = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await _app()(
        {"type": "http", "method": "POST", "path": "/echo", "headers": []},
        receive,
        send,
    )

    assert sent[0]["status"] == 400
