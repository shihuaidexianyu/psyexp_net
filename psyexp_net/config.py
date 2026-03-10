from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

"""配置系统。

按默认值、配置文件、环境变量和显式覆盖依次合并。
"""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置字典。"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce_env_value(raw: str) -> Any:
    """把环境变量字符串尽量转成布尔或数值。"""
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            continue
    return raw


@dataclass(slots=True)
class NetworkConfig:
    host: str = "0.0.0.0"
    bind_host: str = "0.0.0.0"
    control_port: int = 5555
    pub_port: int = 5556
    discovery: str = "manual"
    send_hwm: int = 1000
    recv_hwm: int = 1000
    linger_ms: int = 100
    heartbeat_interval_ms: int = 500
    heartbeat_timeout_ms: int = 2_000
    connect_timeout_ms: int = 2_000
    tcp_keepalive: bool = True
    immediate: bool = True
    router_mandatory: bool = True
    router_handover: bool = True


@dataclass(slots=True)
class ProtocolConfig:
    version: str = "1.0"
    payload_codec: str = "json"
    ack_timeout_ms: int = 800
    max_retries: int = 3
    dedup_cache_size: int = 512


@dataclass(slots=True)
class TimingConfig:
    max_rtt_ms: float = 30.0
    max_jitter_ms: float = 10.0
    max_apply_skew_ms: float = 20.0
    max_clock_offset_ms: float = 15.0
    default_lead_time_ms: int = 120
    ping_history_size: int = 32


@dataclass(slots=True)
class SecurityConfig:
    mode: str = "trusted_lan"
    require_secret: bool = False
    shared_secrets: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    base_dir: str = ".psyexp_net/sessions"
    jsonl: bool = True
    log_payload: bool = False


@dataclass(slots=True)
class ExperimentConfig:
    session_name: str = "default-session"
    experiment_profile: str = "default"
    required_roles: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HealthConfig:
    benchmark_ping_count: int = 25
    benchmark_message_size: int = 256


@dataclass(slots=True)
class AppConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    protocol: ProtocolConfig = field(default_factory=ProtocolConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    experiment: ExperimentConfig = field(default_factory=ExperimentConfig)
    health: HealthConfig = field(default_factory=HealthConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AppConfig":
        # 先展开默认值，再叠加用户配置，避免缺字段时手工补齐。
        defaults = cls()
        merged = _deep_merge(defaults.to_dict(), data)
        return cls(
            network=NetworkConfig(**merged["network"]),
            protocol=ProtocolConfig(**merged["protocol"]),
            timing=TimingConfig(**merged["timing"]),
            security=SecurityConfig(**merged["security"]),
            logging=LoggingConfig(**merged["logging"]),
            experiment=ExperimentConfig(**merged["experiment"]),
            health=HealthConfig(**merged["health"]),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "AppConfig":
        """按后缀解析 TOML、JSON 或 YAML 配置文件。"""
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix in {".toml", ".tml"}:
            data = tomllib.loads(source.read_text(encoding="utf-8"))
        elif suffix == ".json":
            data = json.loads(source.read_text(encoding="utf-8"))
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore
            except ImportError as exc:
                raise RuntimeError("YAML support requires `PyYAML`.") from exc
            data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        else:
            raise ValueError(f"Unsupported config format: {source}")
        return cls.from_mapping(data)

    @classmethod
    def from_sources(
        cls,
        config_path: str | Path | None = None,
        env_prefix: str = "PSYEXP_NET_",
        overrides: dict[str, Any] | None = None,
    ) -> "AppConfig":
        merged: dict[str, Any] = {}
        if config_path is not None:
            merged = cls.from_file(config_path).to_dict()
        env_data: dict[str, Any] = {}
        for key, value in os.environ.items():
            if not key.startswith(env_prefix):
                continue
            # 环境变量格式为 PSYEXP_NET_SECTION__FIELD=value
            parts = key.removeprefix(env_prefix).lower().split("__")
            if len(parts) != 2:
                continue
            section, field_name = parts
            env_data.setdefault(section, {})[field_name] = _coerce_env_value(value)
        if env_data:
            merged = _deep_merge(merged, env_data)
        if overrides:
            merged = _deep_merge(merged, overrides)
        return cls.from_mapping(merged)
