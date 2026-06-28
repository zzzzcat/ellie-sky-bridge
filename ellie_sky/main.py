from __future__ import annotations

import argparse
import hashlib
import logging
import time
from dataclasses import replace
from pathlib import Path

from .capture import capture_window, enable_dpi_awareness, find_window, is_foreground_window
from .chat import ChatController
from .console import configure_logging, print_banner, status as console_status
from .config import load_config
from .diagnostics import Diagnostics
from .input_win import pause_hotkey_pressed
from .interaction import InteractionController
from .ledger import IncomingLedger, MessageLedger
from .memory_chat import MemoryChatReader
from .server import BridgeServer, BridgeState
from .vision import PlayerMessage, VisionClient, VisionObservation


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

    configure_logging()
    config = load_config(args.config)
    print_banner(
        "DRY RUN" if config.safety.dry_run else "LIVE CONTROL",
        config.api.model,
        config.memory_chat.enabled,
        config.game.user_name,
    )
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
    console_status(
        "ST LINK",
        f"Listening on {config.sillytavern.bridge_host}:{config.sillytavern.bridge_port}",
        "green",
    )
    logging.info("Local SillyTavern bridge listening on port %s.", config.sillytavern.bridge_port)
    logging.info("Diagnostics for this run: %s", diagnostics.run_dir)
    diagnostics.event(
        "config_loaded",
        dry_run=config.safety.dry_run,
        poll_seconds=config.game.poll_seconds,
        model=config.api.model,
        user_name=config.game.user_name,
        memory_chat_enabled=config.memory_chat.enabled,
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
    interaction_controller = InteractionController(
        diagnostics,
        config.game.interaction_cooldown_seconds,
        config.safety.dry_run,
    )
    chat_controller = ChatController(
        state,
        ledger,
        incoming_ledger,
        diagnostics,
        config.sillytavern.reply_timeout_seconds,
        config.game.message_limit,
        config.game.message_send_delay_seconds,
        config.game.user_name,
        config.safety.dry_run,
    )
    memory_chat = None
    if config.memory_chat.enabled:
        memory_chat = MemoryChatReader(
            config.memory_chat.local_player_id,
            config.memory_chat.primary_user_id,
            config.game.user_name,
            config.memory_chat.poll_seconds,
            config.memory_chat.friend_names,
        )
    latest_observation = VisionObservation(
        new_messages=[],
        visible_incoming_messages=None,
        scene_narration="她能看到当前的光遇场景，但细节不清楚。",
        interaction_state="我和她目前的互动状态不清楚。",
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

            if memory_chat is not None:
                memory_chat.ensure_process(window.process_id)
                for status in memory_chat.drain_statuses():
                    event_name = status.pop("event")
                    diagnostics.event(event_name, **status)
                    if event_name == "memory_chat_ready":
                        console_status(
                            "MEMORY",
                            f"Linked: {status['ranges']} range(s), "
                            f"{status['range_bytes'] / 1024 / 1024:.1f} MiB",
                            "pink",
                        )
                        logging.info(
                            "Memory chat ready: %s range(s), %.1f MiB polling set.",
                            status["ranges"],
                            status["range_bytes"] / 1024 / 1024,
                        )
                    elif event_name == "memory_chat_discovery_started":
                        console_status(
                            "MEMORY",
                            "Scanning Sky.exe for the chat link...",
                            "pink",
                        )
                    elif event_name == "memory_chat_error":
                        console_status("MEMORY", "Reader error; see diagnostics.", "red")
                        logging.error("Memory chat reader failed: %s", status["error"])
                    elif event_name == "memory_chat_friend_names_updated":
                        logging.info(
                            "Memory chat updated %s friend nickname(s).",
                            len(status["names"]),
                        )
                    elif event_name == "memory_chat_waiting_for_buffer":
                        console_status("MEMORY", "Finding Sky chat buffer...", "yellow")
                        logging.info(
                            "Memory chat buffer is not available yet; retrying discovery."
                        )

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
                        "Send a new target-player message now."
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
                    new_messages=[item.as_dict() for item in observation.new_messages],
                    visible_incoming_messages=(
                        [item.as_dict() for item in observation.visible_incoming_messages]
                        if observation.visible_incoming_messages is not None
                        else None
                    ),
                    scene_narration=observation.scene_narration,
                    interaction_state=observation.interaction_state,
                    friend_tree_panel_open=observation.friend_tree_panel_open,
                    f_prompt_visible=observation.f_prompt_visible,
                    is_friend_tree_star=observation.is_friend_tree_star,
                    interaction_confidence=observation.interaction_confidence,
                )
                if observation.new_messages:
                    diagnostics.event(
                        "vlm_chat_ignored",
                        messages=[item.as_dict() for item in observation.new_messages],
                    )
                latest_scene_narration = (
                    observation.scene_narration
                    or latest_observation.scene_narration
                )
                latest_observation = replace(
                    observation,
                    new_messages=[],
                    visible_incoming_messages=None,
                    scene_narration=latest_scene_narration,
                )
                current_scene_change_score = scene_change_score(
                    last_submitted_scene_sample,
                    current_scene_sample,
                )
                if memory_chat is None:
                    chat_result = chat_controller.process(
                        observation,
                        window.hwnd,
                        scene_changed=current_scene_change_score >= 14.0,
                        scene_change_score=current_scene_change_score,
                    )
                    if chat_result.submitted_any:
                        last_submitted_scene_sample = current_scene_sample
                    interaction_controller.process(
                        latest_observation,
                        requested=chat_result.interaction_requested,
                    )
                else:
                    interaction_controller.process(
                        latest_observation,
                        requested=False,
                    )

            if memory_chat is not None:
                memory_events = memory_chat.drain_events()
                if memory_events:
                    messages = [
                        PlayerMessage(event.sender, event.text)
                        for event in memory_events
                    ]
                    diagnostics.event(
                        "memory_chat_messages",
                        messages=[{
                            "sender_id": event.sender_id,
                            "sender": event.sender,
                            "text": event.text,
                            "msg_id": event.msg_id,
                        } for event in memory_events],
                    )
                    memory_observation = replace(
                        latest_observation,
                        new_messages=messages,
                        visible_incoming_messages=None,
                    )
                    current_scene_sample = scene_sample(screenshot)
                    current_scene_change_score = scene_change_score(
                        last_submitted_scene_sample,
                        current_scene_sample,
                    )
                    chat_result = chat_controller.process(
                        memory_observation,
                        window.hwnd,
                        scene_changed=current_scene_change_score >= 14.0,
                        scene_change_score=current_scene_change_score,
                    )
                    if chat_result.submitted_any:
                        last_submitted_scene_sample = current_scene_sample
                    interaction_controller.process(
                        latest_observation,
                        requested=chat_result.interaction_requested,
                    )

            if args.once:
                break
            time.sleep(config.game.poll_seconds)
    finally:
        diagnostics.event("run_end")
        if memory_chat is not None:
            memory_chat.stop()
        server.close()
