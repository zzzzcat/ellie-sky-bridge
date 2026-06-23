from __future__ import annotations

import argparse
import hashlib
import logging
import time
from pathlib import Path

from .capture import capture_window, enable_dpi_awareness, find_window, is_foreground_window
from .config import load_config
from .diagnostics import Diagnostics
from .input_win import pause_hotkey_pressed, send_chat_message
from .ledger import IncomingLedger, MessageLedger
from .server import BridgeServer, BridgeState
from .text import build_ellie_input, game_speech_chunks, split_ellie_output
from .vision import VisionClient


def image_hash(image) -> str:
    sample = image.resize((256, 256)).convert("RGB")
    ui_mask = bytearray()
    for r, g, b in sample.getdata():
        high = max(r, g, b)
        low = min(r, g, b)
        # Main-screen chat text is light neutral gray. Dark bubbles themselves
        # blend into many scenes, so hash only the light UI/text signal.
        ui_mask.append(255 if high - low < 28 and 135 < high < 245 else 0)
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

    previous_view = None
    last_view_hash: str | None = None
    paused = False
    pause_key_was_down = False
    foreground_warning_logged = False
    window_missing_logged = False
    last_capture_size_warning: tuple[int, int] | None = None
    last_submitted_scene_sample = None
    ledger = MessageLedger()
    incoming_ledger = IncomingLedger(
        config.game.incoming_duplicate_window_seconds,
        processed_ttl_seconds=config.game.incoming_duplicate_window_seconds,
    )

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

            try:
                window = find_window(config.game.window_title, config.game.process_name)
            except RuntimeError as error:
                if not window_missing_logged:
                    logging.info(
                        "Sky window is not available; detection is paused."
                    )
                    diagnostics.event(
                        "detection_paused_window_missing",
                        error=str(error),
                    )
                    window_missing_logged = True
                time.sleep(config.game.poll_seconds)
                continue
            if window_missing_logged:
                logging.info("Sky window is available again; detection resumed.")
                diagnostics.event("detection_resumed_window_available")
                window_missing_logged = False

            if not is_foreground_window(window):
                if not foreground_warning_logged:
                    logging.info(
                        "Sky is not the foreground window; detection is paused."
                    )
                    diagnostics.event("detection_paused_not_foreground")
                    foreground_warning_logged = True
                time.sleep(config.game.poll_seconds)
                continue
            if foreground_warning_logged:
                logging.info("Sky is foreground again; detection resumed.")
                diagnostics.event("detection_resumed_foreground")
                foreground_warning_logged = False

            screenshot = capture_window(window)
            if screenshot.size != (
                config.game.expected_width,
                config.game.expected_height,
            ):
                actual_size = (screenshot.width, screenshot.height)
                if actual_size != last_capture_size_warning:
                    last_capture_size_warning = actual_size
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
            else:
                last_capture_size_warning = None

            current_hash = image_hash(screenshot)
            if current_hash != last_view_hash:
                old_hash = last_view_hash
                last_view_hash = current_hash
                current_scene_sample = scene_sample(screenshot)
                if previous_view is None:
                    previous_view = screenshot.copy()
                    diagnostics.event(
                        "baseline_established",
                        current_hash=current_hash,
                        current_view=diagnostics.save_image(
                            "baseline-current-view",
                            screenshot,
                            jpeg=True,
                        ),
                    )
                    logging.info(
                        "Established main-screen bubble baseline. "
                        "Send a new Big_Bro message now."
                    )
                    if args.once:
                        break
                    time.sleep(config.game.poll_seconds)
                    continue

                vision_started = time.monotonic()
                previous_path = diagnostics.save_image(
                    "vlm-previous-view",
                    previous_view,
                    jpeg=True,
                )
                current_path = diagnostics.save_image(
                    "vlm-current-view",
                    screenshot,
                    jpeg=True,
                )
                diagnostics.event(
                    "vlm_request",
                    previous_hash=old_hash,
                    current_hash=current_hash,
                    previous_view=previous_path,
                    current_view=current_path,
                    recent_outgoing=ledger.recent_outgoing(),
                )
                try:
                    observation = vision.observe_changes(
                        previous_view,
                        screenshot,
                        config.game.user_name,
                        ledger.recent_outgoing(),
                    )
                except Exception as error:
                    logging.error("%s", error)
                    diagnostics.event(
                        "vlm_error",
                        error=repr(error),
                        previous_view=previous_path,
                        current_view=current_path,
                    )
                    if args.once:
                        break
                    time.sleep(config.game.poll_seconds)
                    continue
                previous_view = screenshot.copy()
                logging.info(
                    "Main-bubble/scene VLM completed in %.1f seconds; "
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
                            "still visible in the main game view.",
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
