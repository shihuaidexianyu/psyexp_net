from __future__ import annotations

from dataclasses import dataclass

from psyexp_net.errors import VersionMismatchError

"""协议版本与 capability negotiation。"""


@dataclass(slots=True)
class NegotiatedProtocol:
    version: str
    degraded: bool
    capabilities: list[str]


def parse_protocol_version(version: str) -> tuple[int, int]:
    parts = version.split(".", maxsplit=1)
    if len(parts) != 2:
        raise VersionMismatchError(f"Invalid protocol version: {version}")
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError as exc:
        raise VersionMismatchError(f"Invalid protocol version: {version}") from exc
    return major, minor


def negotiate_protocol(
    server_version: str,
    client_version: str,
    *,
    server_capabilities: list[str],
    client_capabilities: list[str],
) -> NegotiatedProtocol:
    server_major, server_minor = parse_protocol_version(server_version)
    client_major, client_minor = parse_protocol_version(client_version)
    if server_major != client_major:
        raise VersionMismatchError(
            f"Incompatible protocol versions: server={server_version}, client={client_version}"
        )
    negotiated_minor = min(server_minor, client_minor)
    negotiated_version = f"{server_major}.{negotiated_minor}"
    server_set = set(server_capabilities)
    client_set = set(client_capabilities)
    capabilities = sorted(server_set & client_set)
    return NegotiatedProtocol(
        version=negotiated_version,
        degraded=negotiated_version != server_version or negotiated_version != client_version,
        capabilities=capabilities,
    )
