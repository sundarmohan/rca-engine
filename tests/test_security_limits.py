import asyncio

from app.security import RequestLimits


def invoke(middleware, chunks, headers=()):
    messages = []
    requests = iter(chunks)

    async def receive():
        return next(requests)

    async def send(message):
        messages.append(message)

    asyncio.run(middleware({"type": "http", "path": "/api/v1/events", "headers": headers}, receive, send))
    return messages


def test_streaming_body_is_counted_even_with_false_content_length():
    called = []

    async def application(scope, receive, send):
        called.append(True)

    messages = invoke(RequestLimits(application), [
        {"type": "http.request", "body": b"x" * 65536, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": False},
    ], [(b"content-length", b"1")])
    assert messages[0]["status"] == 413
    assert not called


def test_exact_limit_is_replayed_to_application():
    received = []

    async def application(scope, receive, send):
        received.append(await receive())

    invoke(RequestLimits(application), [{"type": "http.request", "body": b"x" * 65536, "more_body": False}])
    assert len(received[0]["body"]) == 65536
    assert received[0]["more_body"] is False


def test_global_request_budget_returns_retry_after():
    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    middleware = RequestLimits(application, requests_per_minute=1)
    assert invoke(middleware, [{"type": "http.request", "body": b"", "more_body": False}])[0]["status"] == 200
    messages = invoke(middleware, [])
    assert messages[0]["status"] == 429
    assert (b"retry-after", b"60") in messages[0]["headers"]
