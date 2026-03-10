class LslBridge:
    """未来 LSL marker 和时序流桥接的占位实现。"""

    def publish_marker(self, marker: str, timestamp: float | None = None) -> None:
        del marker, timestamp
        raise NotImplementedError("LSL integration is not part of the offline MVP.")
