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
            normalized.rfind("。", 0, limit + 1),
            normalized.rfind("！", 0, limit + 1),
            normalized.rfind("？", 0, limit + 1),
            normalized.rfind("；", 0, limit + 1),
            normalized.rfind("，", 0, limit + 1),
            normalized.rfind(". ", 0, limit + 1),
            normalized.rfind("? ", 0, limit + 1),
            normalized.rfind("! ", 0, limit + 1),
            normalized.rfind(" ", 0, limit + 1),
        )
        if cut < 0:
            cut = limit
        elif normalized[cut:cut + 1] in "。！？；，":
            cut += 1
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


def _refer_to_ellie_in_third_person(text: str) -> str:
    value = text.replace("Ellie的", "她的").replace("ellie的", "她的")
    value = value.replace("艾莉的", "她的").replace("艾莉", "她")
    value = re.sub(r"\bEllie's\b", "her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bwith Ellie\b", "with her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bnear Ellie\b", "near her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bbeside Ellie\b", "beside her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bnext to Ellie\b", "next to her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bfacing Ellie\b", "facing her", value, flags=re.IGNORECASE)
    value = re.sub(r"\btoward Ellie\b", "toward her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bbehind Ellie\b", "behind her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bin front of Ellie\b", "in front of her", value, flags=re.IGNORECASE)
    value = re.sub(r"\bEllie\b", "she", value)
    value = re.sub(r"\bellie\b", "she", value)
    value = re.sub(
        r"(^|[.!?]\s+)her\b",
        lambda match: f"{match.group(1)}Her",
        value,
    )
    value = re.sub(
        r"(^|[.!?]\s+)she\b",
        lambda match: f"{match.group(1)}She",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def _join_narration_parts(parts: list[str]) -> str:
    result = ""
    for part in parts:
        if result and not result.endswith(("。", "！", "？", "；", "，")):
            result += " "
        result += part
    return result


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
        parts.append("她能看到当前的光遇场景，但细节不清楚。")
    narration = _refer_to_ellie_in_third_person(_join_narration_parts(parts))
    return f"*{narration}*{message.strip()}"
