class ZeroconfDiscoveryService:
    """zeroconf 发现服务的预留实现。"""

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        try:
            import zeroconf  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("ZeroconfDiscoveryService requires `zeroconf`.") from exc

    def register(self) -> None:
        raise NotImplementedError(
            "Zeroconf integration is reserved for a network-backed milestone."
        )

    def discover(self) -> list[dict[str, str]]:
        raise NotImplementedError(
            "Zeroconf integration is reserved for a network-backed milestone."
        )
