from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .commands import is_interaction_command
from .diagnostics import Diagnostics
from .input_win import send_chat_message
from .ledger import IncomingLedger, MessageLedger
from .server import BridgeState
from .text import build_ellie_input, game_speech_chunks, split_ellie_output
from .vision import VisionObservation


@dataclass(frozen=True)
class ChatProcessingResult:
    submitted_any: bool
    interaction_requested: bool = False


def format_player_messages(messages, user_name: str) -> str:
    return "\n".join(
        message.text
        if message.sender == user_name
        else f"{message.sender}说：{message.text}"
        for message in messages
    )


def is_message_directed_elsewhere(message, user_name: str = "") -> bool:
    return any(marker in message.text for marker in ("@", "＠"))


def is_placeholder_message(text: str) -> bool:
    compact = "".join(character for character in text if not character.isspace())
    return bool(compact) and all(
        character in ".。…·"
        for character in compact
    )


class ChatController:
    def __init__(
        self,
        state: BridgeState,
        ledger: MessageLedger,
        incoming_ledger: IncomingLedger,
        diagnostics: Diagnostics,
        reply_timeout_seconds: float,
        message_limit: int,
        message_send_delay_seconds: float,
        user_name: str,
        dry_run: bool,
    ):
        self.state = state
        self.ledger = ledger
        self.incoming_ledger = incoming_ledger
        self.diagnostics = diagnostics
        self.reply_timeout_seconds = reply_timeout_seconds
        self.message_limit = message_limit
        self.message_send_delay_seconds = message_send_delay_seconds
        self.user_name = user_name
        self.dry_run = dry_run

    def process(
        self,
        observation: VisionObservation,
        window_hwnd: int,
        scene_changed: bool,
        scene_change_score: float,
    ) -> ChatProcessingResult:
        if observation.new_messages:
            logging.info(
                "Scene narration prepared (%s characters).",
                len(observation.scene_narration),
            )
        if observation.visible_incoming_messages is not None:
            self.incoming_ledger.reconcile_visible(
                [message.ledger_key() for message in observation.visible_incoming_messages]
            )

        accepted_messages = []
        interaction_requested = False
        visible_keys = (
            [message.ledger_key() for message in observation.visible_incoming_messages]
            if observation.visible_incoming_messages is not None
            else None
        )
        for message in observation.new_messages:
            if is_placeholder_message(message.text):
                logging.info("Ignored a dot-only placeholder chat message.")
                self.diagnostics.event(
                    "message_decision",
                    sender=message.sender,
                    text=message.text,
                    decision="suppress_placeholder",
                )
                continue
            if (
                message.sender == self.user_name
                and self.ledger.is_outgoing_echo(message.text)
            ):
                logging.warning(
                    "Suppressed an outgoing Ellie message misread as incoming."
                )
                self.diagnostics.event(
                    "message_decision",
                    sender=message.sender,
                    text=message.text,
                    decision="suppress_outgoing_echo",
                )
                continue
            if not self.incoming_ledger.should_process(
                message.ledger_key(),
                visible_keys,
            ):
                logging.warning(
                    "Suppressed a duplicate target-player bubble that is still "
                    "visible in the main game view.",
                )
                self.diagnostics.event(
                    "message_decision",
                    sender=message.sender,
                    text=message.text,
                    decision="suppress_duplicate",
                    visible_incoming_messages=(
                        [item.as_dict() for item in observation.visible_incoming_messages]
                        if observation.visible_incoming_messages is not None
                        else None
                    ),
                )
                continue

            if is_message_directed_elsewhere(message, self.user_name):
                logging.info(
                    "Ignored a primary-user message containing @."
                )
                self.diagnostics.event(
                    "message_decision",
                    sender=message.sender,
                    text=message.text,
                    decision="suppress_addressed_elsewhere",
                )
                continue

            if is_interaction_command(message.text):
                interaction_requested = True
                logging.info("Detected bridge command: %s", message.text)
                self.diagnostics.event(
                    "bridge_command",
                    sender=message.sender,
                    text=message.text,
                    command="interact",
                    decision="request_interaction",
                )
                continue

            accepted_messages.append(message)

        submitted_any = bool(accepted_messages)
        if accepted_messages:
            message_text = format_player_messages(
                accepted_messages,
                self.user_name,
            )
            self.diagnostics.event(
                "message_decision",
                text=message_text,
                messages=[message.as_dict() for message in accepted_messages],
                decision="submit_to_sillytavern",
                scene_narration=observation.scene_narration,
                interaction_state=observation.interaction_state,
                include_scene=scene_changed,
                scene_change_score=round(scene_change_score, 3),
                visible_incoming_messages=(
                    [item.as_dict() for item in observation.visible_incoming_messages]
                    if observation.visible_incoming_messages is not None
                    else None
                ),
            )
            logging.info(
                "Detected %s new named-player message(s) (%s characters).",
                len(accepted_messages),
                len(message_text),
            )
            self._submit_and_send_reply(
                message_text,
                observation,
                window_hwnd,
                scene_changed,
            )

        return ChatProcessingResult(
            submitted_any=submitted_any,
            interaction_requested=interaction_requested,
        )

    def _submit_and_send_reply(
        self,
        message_text: str,
        observation: VisionObservation,
        window_hwnd: int,
        include_scene: bool,
    ) -> None:
        generation_started = time.monotonic()
        request = self.state.submit(build_ellie_input(
            message_text,
            observation.scene_narration,
            observation.interaction_state,
            include_scene=include_scene,
        ))
        if not request.done.wait(self.reply_timeout_seconds):
            logging.error("Timed out waiting for SillyTavern.")
            self.diagnostics.event(
                "sillytavern_timeout",
                text=message_text,
                timeout_seconds=self.reply_timeout_seconds,
            )
            return
        if request.error:
            logging.error("SillyTavern extension error: %s", request.error)
            self.diagnostics.event(
                "sillytavern_error",
                text=message_text,
                error=request.error,
            )
            return

        completed_at = request.completed_at or time.monotonic()
        claimed_at = request.claimed_at or request.submitted_at
        logging.info(
            "SillyTavern pickup took %.2f seconds; "
            "Ellie generation/response took %.1f seconds; "
            "total ST round trip %.1f seconds.",
            max(0.0, claimed_at - request.submitted_at),
            max(0.0, completed_at - claimed_at),
            completed_at - generation_started,
        )
        self.diagnostics.event(
            "sillytavern_reply",
            text=message_text,
            pickup_seconds=round(max(0.0, claimed_at - request.submitted_at), 3),
            generation_seconds=round(max(0.0, completed_at - claimed_at), 3),
            total_seconds=round(completed_at - generation_started, 3),
            reply=request.reply or "",
        )

        speech, actions = split_ellie_output(request.reply or "")
        logging.info(
            "Ellie produced %s speech section(s) and %s action span(s).",
            len(speech),
            len(actions),
        )
        chunks = game_speech_chunks(speech, self.message_limit)
        self.diagnostics.event(
            "ellie_output_split",
            speech=speech,
            actions=actions,
            chunks=chunks,
        )
        for index, chunk in enumerate(chunks, start=1):
            if self.dry_run:
                logging.info(
                    "DRY RUN: would send Sky message %s/%s (%s characters).",
                    index,
                    len(chunks),
                    len(chunk),
                )
                self.diagnostics.event(
                    "sky_send_dry_run",
                    index=index,
                    total=len(chunks),
                    text=chunk,
                )
                continue

            logging.info(
                "Sending Sky message %s/%s (%s characters).",
                index,
                len(chunks),
                len(chunk),
            )
            if not send_chat_message(window_hwnd, chunk):
                logging.error(
                    "Sky did not accept focus for message %s/%s.",
                    index,
                    len(chunks),
                )
                self.diagnostics.event(
                    "sky_send_focus_failed",
                    index=index,
                    total=len(chunks),
                    text=chunk,
                )
                break
            self.ledger.record_outgoing(chunk)
            self.diagnostics.event(
                "sky_send_success",
                index=index,
                total=len(chunks),
                text=chunk,
            )
            time.sleep(self.message_send_delay_seconds)
