from __future__ import annotations

from .enums import ErrorCode

"""公共异常类型。"""


class PsyExpNetError(Exception):
    """Base error for the package."""


class ConfigurationError(PsyExpNetError):
    """Raised for invalid configuration values."""


class ProtocolError(PsyExpNetError):
    """Raised when a message cannot be encoded or processed."""


class VersionMismatchError(ProtocolError):
    """Raised when protocol versions are incompatible."""


class DuplicateClientError(PsyExpNetError):
    code = ErrorCode.DUPLICATE_CLIENT_ID


class AckTimeoutError(PsyExpNetError):
    code = ErrorCode.ACK_TIMEOUT


class QueueOverflowError(PsyExpNetError):
    code = ErrorCode.QUEUE_OVERFLOW


class AuthenticationError(PsyExpNetError):
    code = ErrorCode.AUTH_FAILED
