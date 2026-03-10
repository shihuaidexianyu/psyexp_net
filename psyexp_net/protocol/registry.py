from psyexp_net.enums import MessageType

"""协议层常量注册表。"""

ACK_REQUIRED_TYPES = {
    MessageType.SESSION_START.value,
    MessageType.SESSION_STOP.value,
    MessageType.TRIAL_ARM.value,
    MessageType.TRIAL_START_AT.value,
    MessageType.TRIAL_ABORT.value,
    MessageType.PARAM_UPDATE.value,
}

TERMINAL_TYPES = {
    MessageType.SESSION_STOP.value,
    MessageType.SESSION_ABORT.value,
    MessageType.TRIAL_END.value,
    MessageType.TRIAL_ABORT.value,
}
