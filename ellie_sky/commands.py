from __future__ import annotations

INTERACTION_COMMANDS = {"牵我", "上来"}


def is_interaction_command(text: str) -> bool:
    return any(command in text for command in INTERACTION_COMMANDS)
