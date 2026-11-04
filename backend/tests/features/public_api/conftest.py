"""Shared fixture for the public API tests: a fake DNS and a fake web server for download_cv, so
no test reaches the network.
"""

import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest

CV_HOST = "cv.example.com"
CV_HOST_IP = "93.184.216.34"
CV_URL = f"https://{CV_HOST}/files/cv.txt?token=secret-token"
CV_BYTES = b"Jane Doe\r\njane.doe@example.com"


class FakeWeb:
    """Stands in for DNS (socket.getaddrinfo) and for every httpx.Client the service builds.

    `addresses` maps a host to the IPv4 addresses it resolves to (a host that isn't there is a
    gaierror, an exception value is raised); `handler` answers each request (default: 200 with
    CV_BYTES). `lookups`, `requests` and `client_kwargs` record what the service did.
    """

    def __init__(self) -> None:
        self.addresses: dict[str, list[str] | Exception] = {CV_HOST: [CV_HOST_IP]}
        # An iterator body: a bytes body counts as already read, and iter_raw() refuses it.
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            200, content=iter([CV_BYTES])
        )
        self.lookups: list[tuple[Any, ...]] = []
        self.requests: list[httpx.Request] = []
        self.client_kwargs: list[dict[str, Any]] = []

    def getaddrinfo(self, host: str, port: int, family: int = 0, type: int = 0) -> list[Any]:
        self.lookups.append((host, port, family, type))
        found = self.addresses.get(host)
        if found is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        if isinstance(found, Exception):
            raise found
        return [(family, type, 6, "", (ip, port)) for ip in found]

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
def fake_web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    """Patch socket.getaddrinfo and httpx.Client so downloads go to a FakeWeb."""
    fake = FakeWeb()
    real_client = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        fake.client_kwargs.append(kwargs)
        return real_client(transport=httpx.MockTransport(fake.answer), **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    monkeypatch.setattr(httpx, "Client", client)
    return fake
