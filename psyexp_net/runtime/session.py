from __future__ import annotations

from dataclasses import dataclass

from psyexp_net.enums import SessionState

"""实验会话状态机。"""


@dataclass(slots=True)
class SessionSnapshot:
    session_id: str | None
    state: str
    trial_id: str | None
    phase: str | None
    parameter_version: int


class SessionManager:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.state: str = SessionState.IDLE
        self.current_trial_id: str | None = None
        self.current_phase: str | None = None
        self.parameter_version: int = 0

    def create(self, session_id: str) -> SessionSnapshot:
        """创建 session 并进入等待客户端阶段。"""
        self.session_id = session_id
        self.state = SessionState.WAITING_CLIENTS
        self.current_trial_id = None
        self.current_phase = None
        return self.snapshot()

    def mark_ready(self) -> SessionSnapshot:
        self.state = SessionState.READY
        return self.snapshot()

    def start(self) -> SessionSnapshot:
        self.state = SessionState.RUNNING
        return self.snapshot()

    def pause(self) -> SessionSnapshot:
        self.state = SessionState.PAUSED
        return self.snapshot()

    def stop(self) -> SessionSnapshot:
        self.state = SessionState.STOPPED
        return self.snapshot()

    def abort(self) -> SessionSnapshot:
        self.state = SessionState.ABORTED
        return self.snapshot()

    def arm_trial(self, trial_id: str) -> SessionSnapshot:
        self.current_trial_id = trial_id
        self.current_phase = "armed"
        return self.snapshot()

    def start_trial(self, trial_id: str) -> SessionSnapshot:
        self.current_trial_id = trial_id
        self.current_phase = "running"
        return self.snapshot()

    def end_trial(self, trial_id: str) -> SessionSnapshot:
        if self.current_trial_id == trial_id:
            self.current_phase = "ended"
        return self.snapshot()

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self.session_id,
            state=self.state,
            trial_id=self.current_trial_id,
            phase=self.current_phase,
            parameter_version=self.parameter_version,
        )
