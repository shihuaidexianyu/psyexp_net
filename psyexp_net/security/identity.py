from __future__ import annotations

import uuid
from dataclasses import dataclass, field

"""客户端身份信息。"""


@dataclass(slots=True)
class ClientIdentity:
    client_id: str
    role: str
    instance_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    client_secret: str | None = None
