"""SSRF protection: the crawler only talks to public internet addresses.

A URL from a web page is untrusted input. Before any connection, its host is resolved and every
address is checked: loopback, private, link-local (cloud metadata lives at 169.254.169.254), shared,
reserved, multicast and unspecified ranges are refused, and so are names like `localhost`. The check
runs inside the HTTP client's connect step and the connection is made to the address that was
checked, so a DNS answer that changes between check and connect (rebinding) cannot slip through.

`allow_private=True` exists for the local fixture server of the test suite only; the worker never
sets it.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from typing import Final

import httpcore

BLOCKED_NAMES: Final = frozenset({"localhost", "metadata", "metadata.google.internal"})
BLOCKED_SUFFIXES: Final = (".localhost", ".local", ".internal", ".home.arpa", ".lan")


class UnsafeTargetError(Exception):
    """The host is not a public internet address; nothing was sent to it."""


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    """Every address the host resolves to. Split out so tests can simulate a rebinding answer."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def public_addresses(host: str, port: int, *, allow_private: bool) -> list[str]:
    """The addresses to connect to. Raises `UnsafeTargetError` if any of them is not public."""
    name = host.strip("[]").rstrip(".").lower()
    if not allow_private and (name in BLOCKED_NAMES or name.endswith(BLOCKED_SUFFIXES)):
        raise UnsafeTargetError(f"{name} is a local name")
    try:
        addresses = [str(ipaddress.ip_address(name))]
    except ValueError:
        try:
            addresses = await resolve(name, port)
        except OSError as exc:
            raise socket.gaierror(f"{name} does not resolve: {exc}") from exc
    if not addresses:
        raise socket.gaierror(f"{name} does not resolve")
    if not allow_private:
        refused = [address for address in addresses if not is_public(address)]
        if refused:
            raise UnsafeTargetError(f"{name} resolves to a non-public address ({refused[0]})")
    return addresses


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """httpcore's own backend, except that it connects to a checked address and nothing else.

    TLS still verifies the certificate against the original host name: httpcore passes the origin
    host to `start_tls`, not the address used here.
    """

    def __init__(self, *, allow_private: bool) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._allow_private = allow_private

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await public_addresses(host, port, allow_private=self._allow_private)
        return await self._inner.connect_tcp(
            addresses[0], port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's signature
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeTargetError("unix sockets are never used by the crawler")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
