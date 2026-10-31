from collections import deque
import threading
import time

from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app, maximum_bytes=65536, requests_per_minute=240):
        self.app = app
        self.maximum_bytes = maximum_bytes
        self.requests_per_minute = requests_per_minute
        self._requests = deque()
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        now = time.monotonic()
        with self._lock:
            while self._requests and self._requests[0] <= now - 60:
                self._requests.popleft()
            allowed = len(self._requests) < self.requests_per_minute
            if allowed:
                self._requests.append(now)
        if not allowed:
            return await JSONResponse({"detail": "API request budget exhausted; retry later"}, status_code=429, headers={"Retry-After": "60"})(scope, receive, send)
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        if length is not None:
            if not length.isdigit():
                return await JSONResponse({"detail": "Invalid request length"}, status_code=400)(scope, receive, send)
            if int(length) > self.maximum_bytes:
                return await JSONResponse({"detail": "Request exceeds 64 KiB limit"}, status_code=413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum_bytes:
                return await JSONResponse({"detail": "Request exceeds 64 KiB limit"}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)
