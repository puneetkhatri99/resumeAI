"""Per-IP rate limiting for every /api request: an in-memory sliding window, no package needed.

Two limits (user decision, 2026-10-27): 120 /api requests per minute overall, plus 30 uploads
(POST /api/resume_intake) per hour, counted on top of the overall limit. A request over either
gets 429 with a Retry-After header. The counts live in this process's memory, so they reset on
a restart and aren't shared between instances.

The client IP is request.client.host. Behind a proxy (Render) that is the proxy's address
unless uvicorn is set up to trust its forwarded header; the deploy step sets that up.
"""

import math
import threading
from collections import deque
from time import monotonic

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

API_PREFIX = "/api"
UPLOAD_PATH = "/api/resume_intake"
API_LIMIT = 120
API_WINDOW_SECONDS = 60
UPLOAD_LIMIT = 30
UPLOAD_WINDOW_SECONDS = 3600
# Each bucket's (limit, window in seconds). Every /api request counts in "api"; an upload also
# counts in "upload".
BUCKETS: dict[str, tuple[int, int]] = {
    "api": (API_LIMIT, API_WINDOW_SECONDS),
    "upload": (UPLOAD_LIMIT, UPLOAD_WINDOW_SECONDS),
}
# How often every key is pruned, so the IPs that stop sending don't stay in memory.
SWEEP_INTERVAL_SECONDS = 60


class RateLimiter:
    """Sliding-window request times per (client IP, bucket), in memory, behind one lock.

    Only allowed requests are recorded, so a client that waits for Retry-After gets in.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._last_sweep = monotonic()

    def check(self, client_ip: str, method: str, path: str) -> int | None:
        """Count one /api request from `client_ip`.

        Returns None when it is allowed (and records it), or the whole seconds until it would
        be allowed (and records nothing).
        """
        buckets = ["api", "upload"] if method == "POST" and path == UPLOAD_PATH else ["api"]
        now = monotonic()
        with self._lock:
            if now - self._last_sweep >= SWEEP_INTERVAL_SECONDS:
                self._sweep(now)
            waits = []
            for bucket in buckets:
                limit, window = BUCKETS[bucket]
                hits = self._hits.get((client_ip, bucket), deque())
                prune(hits, now, window)
                if len(hits) >= limit:
                    # The oldest request leaves the window first.
                    waits.append(math.ceil(hits[0] + window - now))
            if waits:
                return max(1, *waits)
            for bucket in buckets:
                self._hits.setdefault((client_ip, bucket), deque()).append(now)
        return None

    def reset(self) -> None:
        """Forget every count (tests start each case from zero)."""
        with self._lock:
            self._hits.clear()
            self._last_sweep = monotonic()

    def _sweep(self, now: float) -> None:
        """Prune every key and drop the empty ones, so memory stays bounded. Hold the lock."""
        for key, hits in list(self._hits.items()):
            prune(hits, now, BUCKETS[key[1]][1])
            if not hits:
                del self._hits[key]
        self._last_sweep = now


def prune(hits: deque[float], now: float, window: int) -> None:
    """Drop the request times that have left the window (oldest first, so from the left)."""
    while hits and hits[0] <= now - window:
        hits.popleft()


# The app's one limiter; RateLimitMiddleware uses it and tests reset it.
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Answer 429 to an /api request over its client IP's limit; pass every other request on.

    BaseHTTPMiddleware passes streaming responses (the upload's event stream) through as they
    come.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Check the limits before the request reaches the app (and before its body is read)."""
        path = request.url.path
        if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
            client_ip = request.client.host if request.client else "unknown"
            retry_after = rate_limiter.check(client_ip, request.method, path)
            if retry_after is not None:
                return JSONResponse(
                    {"detail": f"Too many requests. Try again in {retry_after} seconds."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)
