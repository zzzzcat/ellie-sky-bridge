from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    model: str
    key_env: str
    timeout_seconds: float


@dataclass(frozen=True)
class GameConfig:
    window_title: str
    process_name: str
    expected_width: int
    expected_height: int
    chat_toggle_key: str
    chat_send_key: str
    message_limit: int
    message_send_delay_seconds: float
    panel_restore_cooldown_seconds: float
    incoming_duplicate_window_seconds: float
    user_name: str
    poll_seconds: float
    panel_open_delay_seconds: float


@dataclass(frozen=True)
class SillyTavernConfig:
    bridge_host: str
    bridge_port: int
    expected_character: str
    reply_timeout_seconds: float


@dataclass(frozen=True)
class SafetyConfig:
    pause_hotkey: str
    dry_run: bool


@dataclass(frozen=True)
class Config:
    api: ApiConfig
    game: GameConfig
    sillytavern: SillyTavernConfig
    safety: SafetyConfig


def load_config(path: str | Path) -> Config:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return Config(
        api=ApiConfig(**data["api"]),
        game=GameConfig(**data["game"]),
        sillytavern=SillyTavernConfig(**data["sillytavern"]),
        safety=SafetyConfig(**data["safety"]),
    )
