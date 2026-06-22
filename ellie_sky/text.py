from __future__ import annotations

import re


def split_ellie_output(text: str) -> tuple[list[str], list[str]]:
    """
    Return speech chunks and action spans.

    Nephra uses single asterisks for narration. Unmatched asterisks are treated
    as ordinary speech so malformed output is not silently lost.
    """
    speech: list[str] = []
    actions: list[str] = []
    cursor = 0
    for match in re.finditer(r"\*([^*]+)\*", text, flags=re.DOTALL):
        before = text[cursor:match.start()].strip()
        if before:
            speech.append(before)
        action = match.group(1).strip()
        if action:
            actions.append(action)
        cursor = match.end()
    remainder = text[cursor:].strip()
    if remainder:
        speech.append(remainder)
    return speech, actions


def split_for_game(text: str, limit: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    while len(normalized) > limit:
        cut = max(
            normalized.rfind(". ", 0, limit + 1),
            normalized.rfind("? ", 0, limit + 1),
            normalized.rfind("! ", 0, limit + 1),
            normalized.rfind(" ", 0, limit + 1),
        )
        if cut <= 0:
            cut = limit
        else:
            cut += 1
        chunks.append(normalized[:cut].strip())
        normalized = normalized[cut:].strip()
    if normalized:
        chunks.append(normalized)
    return chunks


def game_speech_chunks(speech: list[str], limit: int) -> list[str]:
    """Keep each spoken section separate, splitting only when it exceeds limit."""
    return [
        chunk
        for section in speech
        for chunk in split_for_game(section, limit)
    ]


def _clean_narration(text: str) -> str:
    value = re.sub(r"[*\r\n]+", " ", text)
    return re.sub(r"\s+", " ", value).strip()


def build_ellie_input(
    message: str,
    scene_narration: str,
    interaction_state: str = "",
    include_scene: bool = True,
) -> str:
    parts: list[str] = []
    if include_scene:
        narration = _clean_narration(scene_narration)
        if narration:
            parts.append(narration)
    interaction = _clean_narration(interaction_state)
    if interaction:
        parts.append(interaction)
    if not parts:
        parts.append("Ellie can see the current Sky scene, but the details are unclear.")
    narration = " ".join(parts)
    return f"*{narration}*\n{message.strip()}"
