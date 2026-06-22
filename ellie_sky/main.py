from __future__ import annotations

import argparse
import hashlib
import logging
import time
from pathlib import Path

from .capture import capture_window, enable_dpi_awareness, find_window
from .config import load_config
from .diagnostics import Diagnostics
from .input_win import focus_window, pause_hotkey_pressed, press_key, send_chat_message
from .ledger import IncomingLedger, MessageLedger
from .panel import crop_chat_column, is_chat_panel_open
from .server import BridgeServer, BridgeState
from .text import build_ellie_input, game_speech_chunks, split_ellie_output
from .vision import VisionClient


def image_hash(image) -> str:
    sample = image.resize((256, 256)).convert("RGB")
    ui_mask = bytearray()
    for r, g, b in sample.getdata():
        high = max(r, g, b)
        low = min(r, g, b)
        # Sky's chat text and bubbles are close to neutral gray/white. Most
        # moving game scenery is saturated, so excluding it prevents constant
        # VLM calls while the camera or particles move.
        ui_mask.append(255 if high - low < 32 and high > 105 else 0)
    return hashlib.sha256(ui_mask).hexdigest()


def scene_sample(image):
    width, height = image.size
    # Compare the play area rather than the left chat panel or bottom input UI.
    crop = image.crop((
        round(width * 0.47),
        0,
        width,
        round(height * 0.88),
    ))
    return crop.resize((64, 64)).convert("RGB")


def scene_change_score(previous, current) -> float:
    if previous is None:
        return float("inf")
    previous_bytes = previous.tobytes()
    current_bytes = current.tobytes()
    total = sum(
        abs(old - new)
        for old, new in zip(previous_bytes, current_bytes, strict=True)
    )
    return total / len(previous_bytes)


def scene_context_changed(previous, current, threshold: float = 14.0) -> bool:
    return scene_change_score(previous, current) >= threshold


def new_visible_messages(previous, current):
    """Find messages appended after the largest previous/current overlap."""
    if not previous:
        return []
    previous_keys = [
        (item.sender, item.text, item.direction, item.raw_text)
        for item in previous
    ]
    current_keys = [
        (item.sender, item.text, item.direction, item.raw_text)
        for item in current
    ]
    max_overlap = min(len(previous), len(current))
    for size in range(max_overlap, 0, -1):
        if previous_keys[-size:] == current_keys[:size]:
            return current[size:]
    return current


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true", help="Run one capture cycle.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = load_config(args.config)
    state_dir = Path(__file__).resolve().parents[1] / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    diagnostics = Diagnostics(state_dir / "diagnostics")
    enable_dpi_awareness()

    vision = VisionClient(
        config.api.base_url,
        config.api.model,
        config.api.key_env,
        config.api.timeout_seconds,
    )
    state = BridgeState()
    server = BridgeServer(
        config.sillytavern.bridge_host,
        config.sillytavern.bridge_port,
        state,
    )
    server.start()
    logging.info("Local SillyTavern bridge listening on port %s.", config.sillytavern.bridge_port)
    logging.info("Diagnostics for this run: %s", diagnostics.run_dir)
    diagnostics.event(
        "config_loaded",
        dry_run=config.safety.dry_run,
        poll_seconds=config.game.poll_seconds,
        model=config.api.model,
        expected_size=[
            config.game.expected_width,
            config.game.expected_height,
        ],
    )

    previous_column = None
    last_column_hash: str | None = None
    paused = False
    pause_key_was_down = False
    last_panel_restore = 0.0
    last_submitted_scene_sample = None
    ledger = MessageLedger()
    incoming_ledger = IncomingLedger(config.game.incoming_duplicate_window_seconds)

    try:
        while True:
            pause_key_is_down = pause_hotkey_pressed()
            if pause_key_is_down and not pause_key_was_down:
                paused = not paused
                logging.warning("Bridge %s.", "paused" if paused else "resumed")
            pause_key_was_down = pause_key_is_down
            if paused:
                time.sleep(0.1)
                continue

            window = find_window(config.game.window_title, config.game.process_name)
            screenshot = capture_window(window)
            if screenshot.size != (
                config.game.expected_width,
                config.game.expected_height,
            ):
                logging.warning(
                    "Game capture is %sx%s; expected %sx%s.",
                    screenshot.width,
                    screenshot.height,
                    config.game.expected_width,
                    config.game.expected_height,
                )
                diagnostics.event(
                    "capture_size_warning",
                    actual_size=[screenshot.width, screenshot.height],
                    expected_size=[
                        config.game.expected_width,
                        config.game.expected_height,
                    ],
                )

            if not is_chat_panel_open(screenshot):
                now = time.monotonic()
                if now - last_panel_restore < config.game.panel_restore_cooldown_seconds:
                    diagnostics.event(
                        "panel_closed_cooldown",
                        seconds_since_restore=round(now - last_panel_restore, 3),
                    )
                    time.sleep(config.game.poll_seconds)
                    continue
                last_panel_restore = now
                logging.info("Chat panel is closed; restoring it with C.")
                diagnostics.event("panel_restore_attempt")
                focused = focus_window(window.hwnd)
                if not focused:
                    logging.warning(
                        "Windows did not grant foreground focus to Sky; "
                        "panel restore skipped for this cycle."
                    )
                    diagnostics.event("panel_restore_focus_failed")
                    time.sleep(config.game.poll_seconds)
                    continue
                press_key(config.game.chat_toggle_key)
                time.sleep(config.game.panel_open_delay_seconds)
                screenshot = capture_window(window)
                if not is_chat_panel_open(screenshot):
                    failure_path = state_dir / "panel-failure.png"
                    screenshot.save(failure_path)
                    logging.warning(
                        "C was sent, but the chat panel is still not detected. "
                        "Saved the current game capture to %s.",
                        failure_path,
                    )
                    diagnostics.event(
                        "panel_restore_failed",
                        screenshot=diagnostics.save_image(
                            "panel-failure",
                            screenshot,
                            jpeg=True,
                        ),
                    )
                    if args.once:
                        break
                    time.sleep(config.game.poll_seconds)
                    continue
                logging.info("Chat panel is open.")
                diagnostics.event("panel_restore_succeeded")

            column = crop_chat_column(screenshot)
            current_hash = image_hash(column)
            if current_hash != last_column_hash:
                old_hash = last_column_hash
                last_column_hash = current_hash
                current_scene_sample = scene_sample(screenshot)
                if previous_column is None:
                    previous_column = column.copy()
                    diagnostics.event(
                        "baseline_established",
                        current_hash=current_hash,
                        current_column=diagnostics.save_image(
                            "baseline-current-column",
                            column,
                        ),
                        current_scene=diagnostics.save_image(
                            "baseline-current-scene",
                            screenshot,
                            jpeg=True,
                        ),
                    )
                    logging.info(
                        "Established visual chat baseline. "
                        "Send a new Big_Bro message now."
                    )
                    if args.once:
                        break
                    time.sleep(config.game.poll_seconds)
                    continue

                vision_started = time.monotonic()
                previous_path = diagnostics.save_image(
                    "vlm-previous-column",
                    previous_column,
                )
                current_path = diagnostics.save_image(
                    "vlm-current-column",
                    column,
                )
                scene_path = diagnostics.save_image(
                    "vlm-current-scene",
                    screenshot,
                    jpeg=True,
                )
                diagnostics.event(
                    "vlm_request",
                    previous_hash=old_hash,
                    current_hash=current_hash,
                    previous_column=previous_path,
                    current_column=current_path,
                    current_scene=scene_path,
                    recent_outgoing=ledger.recent_outgoing(),
                )
                try:
                    observation = vision.observe_changes(
                        previous_column,
                        column,
                        screenshot,
                        config.game.user_name,
                        ledger.recent_outgoing(),
                    )
                except Exception as error:
                    logging.error("%s", error)
                    diagnostics.event(
                        "vlm_error",
                        error=repr(error),
                        previous_column=previous_path,
                        current_column=current_path,
                        current_scene=scene_path,
                    )
                    if args.once:
                        break
                    time.sleep(config.game.poll_seconds)
                    continue
                previous_column = column.copy()
                logging.info(
                    "Combined chat/scene VLM completed in %.1f seconds; "
                    "found %s new incoming message(s).",
                    time.monotonic() - vision_started,
                    len(observation.new_messages),
                )
                diagnostics.event(
                    "vlm_response",
                    elapsed_seconds=round(time.monotonic() - vision_started, 3),
                    raw_response=observation.raw_response,
                    parsed_response=observation.parsed_response,
                    new_messages=observation.new_messages,
                    visible_incoming_messages=observation.visible_incoming_messages,
                    scene_narration=observation.scene_narration,
                    interaction_state=observation.interaction_state,
                )
                if observation.new_messages:
                    logging.info(
                        "Scene narration prepared (%s characters).",
                        len(observation.scene_narration),
                    )
                if observation.visible_incoming_messages is not None:
                    incoming_ledger.reconcile_visible(
                        observation.visible_incoming_messages
                    )
                for message_text in observation.new_messages:
                    if ledger.is_outgoing_echo(message_text):
                        logging.warning(
                            "Suppressed an outgoing Ellie message misread as incoming."
                        )
                        diagnostics.event(
                            "message_decision",
                            text=message_text,
                            decision="suppress_outgoing_echo",
                        )
                        continue
                    if not incoming_ledger.should_process(
                        message_text,
                        observation.visible_incoming_messages,
                    ):
                        logging.warning(
                            "Suppressed a duplicate Big_Bro bubble that is "
                            "still visible in chat history.",
                        )
                        diagnostics.event(
                            "message_decision",
                            text=message_text,
                            decision="suppress_duplicate",
                            visible_incoming_messages=(
                                observation.visible_incoming_messages
                            ),
                        )
                        continue
                    diagnostics.event(
                        "message_decision",
                        text=message_text,
                        decision="submit_to_sillytavern",
                        scene_narration=observation.scene_narration,
                        interaction_state=observation.interaction_state,
                        include_scene=scene_context_changed(
                            last_submitted_scene_sample,
                            current_scene_sample,
                        ),
                        scene_change_score=round(
                            scene_change_score(
                                last_submitted_scene_sample,
                                current_scene_sample,
                            ),
                            3,
                        ),
                        visible_incoming_messages=observation.visible_incoming_messages,
                    )
                    logging.info(
                        "Detected a new Big_Bro message (%s characters).",
                        len(message_text),
                    )
                    generation_started = time.monotonic()
                    include_scene = scene_context_changed(
                        last_submitted_scene_sample,
                        current_scene_sample,
                    )
                    request = state.submit(build_ellie_input(
                        message_text,
                        observation.scene_narration,
                        observation.interaction_state,
                        include_scene=include_scene,
                    ))
                    last_submitted_scene_sample = current_scene_sample
                    if not request.done.wait(config.sillytavern.reply_timeout_seconds):
                        logging.error("Timed out waiting for SillyTavern.")
                        diagnostics.event(
                            "sillytavern_timeout",
                            text=message_text,
                            timeout_seconds=config.sillytavern.reply_timeout_seconds,
                        )
                        continue
                    if request.error:
                        logging.error("SillyTavern extension error: %s", request.error)
                        diagnostics.event(
                            "sillytavern_error",
                            text=message_text,
                            error=request.error,
                        )
                        continue
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
                    diagnostics.event(
                        "sillytavern_reply",
                        text=message_text,
                        pickup_seconds=round(
                            max(0.0, claimed_at - request.submitted_at),
                            3,
                        ),
                        generation_seconds=round(
                            max(0.0, completed_at - claimed_at),
                            3,
                        ),
                        total_seconds=round(completed_at - generation_started, 3),
                        reply=request.reply or "",
                    )

                    speech, actions = split_ellie_output(request.reply or "")
                    logging.info(
                        "Ellie produced %s speech section(s) and %s action span(s).",
                        len(speech),
                        len(actions),
                    )
                    chunks = game_speech_chunks(speech, config.game.message_limit)
                    diagnostics.event(
                        "ellie_output_split",
                        speech=speech,
                        actions=actions,
                        chunks=chunks,
                    )
                    for index, chunk in enumerate(chunks, start=1):
                        if config.safety.dry_run:
                            logging.info(
                                "DRY RUN: would send Sky message %s/%s (%s characters).",
                                index,
                                len(chunks),
                                len(chunk),
                            )
                            diagnostics.event(
                                "sky_send_dry_run",
                                index=index,
                                total=len(chunks),
                                text=chunk,
                            )
                        else:
                            logging.info(
                                "Sending Sky message %s/%s (%s characters).",
                                index,
                                len(chunks),
                                len(chunk),
                            )
                            current = capture_window(window)
                            if not is_chat_panel_open(current):
                                if not focus_window(window.hwnd):
                                    logging.error(
                                        "Could not focus Sky before message %s/%s.",
                                        index,
                                        len(chunks),
                                    )
                                    break
                                press_key(config.game.chat_toggle_key)
                                time.sleep(config.game.panel_open_delay_seconds)
                                current = capture_window(window)
                            if not is_chat_panel_open(current):
                                logging.error(
                                    "Chat panel did not open before message %s/%s; "
                                    "remaining segments were not sent.",
                                    index,
                                    len(chunks),
                                )
                                diagnostics.event(
                                    "sky_send_panel_closed",
                                    index=index,
                                    total=len(chunks),
                                    text=chunk,
                                    screenshot=diagnostics.save_image(
                                        "send-panel-closed",
                                        current,
                                        jpeg=True,
                                    ),
                                )
                                break
                            if not send_chat_message(window.hwnd, chunk):
                                logging.error(
                                    "Sky did not accept focus for message %s/%s.",
                                    index,
                                    len(chunks),
                                )
                                diagnostics.event(
                                    "sky_send_focus_failed",
                                    index=index,
                                    total=len(chunks),
                                    text=chunk,
                                )
                                break
                            ledger.record_outgoing(chunk)
                            diagnostics.event(
                                "sky_send_success",
                                index=index,
                                total=len(chunks),
                                text=chunk,
                            )
                            time.sleep(config.game.message_send_delay_seconds)
                            # Do not advance the incoming baseline here. Big Bro
                            # may have spoken while Ellie was generating or
                            # while these outgoing segments were being sent.
                            # The next visual comparison must still see those
                            # intervening messages. Ellie's own new bubbles are
                            # excluded by the outgoing ledger.

            if args.once:
                break
            time.sleep(config.game.poll_seconds)
    finally:
        diagnostics.event("run_end")
        server.close()
