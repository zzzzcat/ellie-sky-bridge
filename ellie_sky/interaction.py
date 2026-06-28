from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .diagnostics import Diagnostics
from .input_win import press_key
from .vision import VisionObservation


def active_together_state(interaction_state: str) -> bool:
    normalized = interaction_state.lower()
    active_markers = [
        "carrying her",
        "carry her",
        "on my back",
        "on my shoulders",
        "holding hands",
        "holding her hand",
        "princess carry",
        "hugging",
        "sitting together",
        "flying together",
        "背着她",
        "她在我背上",
        "她骑在我肩上",
        "牵着手",
        "牵着她的手",
        "公主抱",
        "抱着她",
        "拥抱",
        "一起坐着",
        "一起飞行",
    ]
    return any(marker in normalized for marker in active_markers)


@dataclass(frozen=True)
class InteractionDecision:
    should_press: bool
    reason: str


class InteractionController:
    def __init__(
        self,
        diagnostics: Diagnostics | None = None,
        cooldown_seconds: float = 0.0,
        dry_run: bool = True,
    ):
        self.diagnostics = diagnostics
        self.cooldown_seconds = cooldown_seconds
        self.dry_run = dry_run
        self.last_pressed = 0.0
        self.request_until = 0.0

    def request_interaction(self, window_seconds: float = 12.0) -> None:
        self.request_until = max(self.request_until, time.monotonic() + window_seconds)
        if self.diagnostics is not None:
            self.diagnostics.event(
                "interaction_request",
                window_seconds=window_seconds,
            )

    def process(
        self,
        observation: VisionObservation,
        requested: bool = False,
    ) -> InteractionDecision:
        if requested:
            self.request_interaction()
        now = time.monotonic()
        cooldown_ready = now - self.last_pressed >= self.cooldown_seconds
        request_active = now <= self.request_until
        decision = self.decide(observation, cooldown_ready, request_active)
        if decision.should_press:
            self.last_pressed = time.monotonic()
            key = "esc" if decision.reason == "close_friend_tree_panel" else "f"
            if key == "f":
                self.request_until = 0.0
            if self.dry_run:
                logging.info(
                    "DRY RUN: would press %s for interaction recovery.",
                    key.upper(),
                )
                self._event(
                    "interaction_dry_run",
                    observation,
                    key=key,
                    reason=decision.reason,
                )
            else:
                logging.info("Pressing %s for interaction recovery.", key.upper())
                press_key(key)
                self._event(
                    "interaction_press",
                    observation,
                    key=key,
                    reason=decision.reason,
                )
        else:
            self._event("interaction_skip", observation, reason=decision.reason)
        return decision

    def decide(
        self,
        observation: VisionObservation,
        cooldown_ready: bool,
        request_active: bool = True,
    ) -> InteractionDecision:
        if observation.friend_tree_panel_open:
            return InteractionDecision(True, "close_friend_tree_panel")
        if not request_active:
            return InteractionDecision(False, "interaction_not_requested")
        if not observation.f_prompt_visible:
            return InteractionDecision(False, "no_prompt")
        if observation.is_friend_tree_star is True:
            return InteractionDecision(False, "friend_tree_star")
        if observation.is_friend_tree_star is None:
            return InteractionDecision(False, "unclear_icon")
        if observation.interaction_confidence < 0.75:
            return InteractionDecision(False, "low_confidence")
        if active_together_state(observation.interaction_state):
            return InteractionDecision(False, "active_together_state")
        if not cooldown_ready:
            return InteractionDecision(False, "cooldown")
        return InteractionDecision(True, "press_non_friend_tree_prompt")

    def _event(
        self,
        name: str,
        observation: VisionObservation,
        **extra,
    ) -> None:
        if self.diagnostics is None:
            return
        self.diagnostics.event(
            name,
            f_prompt_visible=observation.f_prompt_visible,
            friend_tree_panel_open=observation.friend_tree_panel_open,
            is_friend_tree_star=observation.is_friend_tree_star,
            confidence=observation.interaction_confidence,
            **extra,
        )
