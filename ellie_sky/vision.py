from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass

import requests
from PIL import Image


def is_english_chat_message(text: str) -> bool:
    """Accept ordinary English chat while rejecting CJK-directed messages."""
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text):
        return False
    letters = re.findall(r"[A-Za-z]", text)
    return len(letters) >= 2


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    direction: str = "unknown"
    has_sender_suffix: bool = False
    x_center: float = 0.5
    raw_text: str = ""

    def is_incoming_from(self, user_name: str) -> bool:
        suffix_pattern = re.compile(
            rf"\s*-\s*{re.escape(user_name)}\s*$",
            flags=re.IGNORECASE,
        )
        visual_incoming = self.direction == "incoming" and self.x_center < 0.5
        suffix_evidence = bool(suffix_pattern.search(self.raw_text))
        structured_evidence = self.sender == user_name and self.has_sender_suffix
        return visual_incoming and (suffix_evidence or structured_evidence)


@dataclass(frozen=True)
class VisionObservation:
    new_messages: list[str]
    visible_incoming_messages: list[str] | None
    scene_narration: str
    interaction_state: str
    raw_response: str = ""
    parsed_response: dict | None = None


def _image_data_url(
    image: Image.Image,
    max_size: tuple[int, int] = (1536, 1536),
    quality: int = 88,
) -> str:
    image = image.copy()
    image.thumbnail(max_size)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    return json.loads(text)


class VisionClient:
    def __init__(self, base_url: str, model: str, key_env: str, timeout: float):
        api_key = os.environ.get(key_env)
        if not api_key:
            raise RuntimeError(f"Set the {key_env} environment variable before starting.")
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def read_chat(self, image: Image.Image, user_name: str) -> list[ChatMessage]:
        prompt = f"""
Read the visible Sky: Children of the Light chat-history column.
The game UI is Chinese, but messages may be English or Chinese.

Return strict JSON only:
{{
  "messages": [
    {{
      "sender": "exact visible sender, or empty for Ellie's own message",
      "raw_text": "all text visibly printed in the bubble, including any sender suffix",
      "text": "message body without the sender suffix",
      "direction": "incoming or outgoing",
      "has_sender_suffix": true,
      "x_center": 0.25
    }}
  ]
}}

Rules:
- Preserve English exactly, including punctuation.
- Read messages from top to bottom.
- Ignore the chat input placeholder and UI labels.
- An incoming message is a DARK bubble aligned on the LEFT. Its visible text
  ends with a sender suffix such as " - {user_name}".
- An outgoing message is a LIGHT bubble aligned on the RIGHT. It has NO sender
  suffix. These are Ellie's own messages and must NEVER be labeled as {user_name}.
- Remove " - {user_name}" from returned incoming text. For that bubble set
  sender="{user_name}", direction="incoming", and has_sender_suffix=true.
  Preserve the suffix in raw_text.
- For outgoing right-side bubbles set sender="", direction="outgoing", and
  has_sender_suffix=false. Preserve their visible message in raw_text.
- x_center is the horizontal center of the bubble divided by image width.
  The left edge is 0.0 and the right edge is 1.0.
- Ignore any sender shown as \u964c\u751f\u4eba.
- Ignore messages whose text consists only of dots or ellipses.
- The target user's exact nickname is {user_name}.
- Do not translate message text.
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                ],
            }],
            "temperature": 0,
        }
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )

        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        messages: list[ChatMessage] = []
        suffix_pattern = re.compile(
            rf"\s*-\s*{re.escape(user_name)}\s*$",
            flags=re.IGNORECASE,
        )
        for item in data.get("messages", []):
            sender = str(item.get("sender", "")).strip()
            text = str(item.get("text", "")).strip()
            raw_text = str(item.get("raw_text", text)).strip()
            direction = str(item.get("direction", "unknown")).strip().lower()
            reported_suffix = item.get("has_sender_suffix") is True
            try:
                x_center = float(item.get("x_center", 0.5))
            except (TypeError, ValueError):
                x_center = 0.5

            if not text or sender == "\u964c\u751f\u4eba":
                continue
            if not text.strip(".\u2026 "):
                continue

            visible_suffix = bool(suffix_pattern.search(raw_text))
            text = suffix_pattern.sub("", text).strip()
            messages.append(ChatMessage(
                sender=sender,
                text=text,
                direction=direction,
                has_sender_suffix=reported_suffix or visible_suffix,
                x_center=x_center,
                raw_text=raw_text,
            ))
        return messages

    def read_new_incoming(
        self,
        previous_chat_image: Image.Image,
        current_chat_image: Image.Image,
        user_name: str,
        recent_outgoing: list[str] | None = None,
    ) -> list[str]:
        outgoing_json = json.dumps(recent_outgoing or [], ensure_ascii=False)
        prompt = f"""
Compare two chronological screenshots of the Sky: Children of the Light
chat-history panel.

Image 1 is the BEFORE chat-history panel.
Image 2 is the AFTER chat-history panel.

Return only messages that appeared newly in Image 2:
- Return EVERY qualifying new message, in visual top-to-bottom order. Do not
  return only the newest one.
- The bubble is dark and aligned on the LEFT.
- Its visible text ends with the sender suffix "- {user_name}".
- It is not already visible in Image 1.
- The message body is English. Reject any message containing Chinese,
  Japanese, or Korean characters.
- Exclude all light RIGHT-side bubbles. Those are Ellie's own messages.
- The bridge recently sent these Ellie messages:
  {outgoing_json}
  Never return any of them, even if wrapping, punctuation, or OCR differs.
- Exclude moved, reflowed, or scrolled copies of old messages.
- Remove the trailing sender suffix from returned message text.
- Preserve the English message body exactly.
- Ignore messages from \u964c\u751f\u4eba and dot-only messages.

Return strict JSON only:
{{"new_messages":["exact message body"]}}

If uncertain whether a message is genuinely new, return no message.
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(previous_chat_image)},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(current_chat_image)},
                    },
                ],
            }],
            "temperature": 0,
        }
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )
        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        messages = []
        for value in data.get("new_messages", []):
            text = str(value).strip()
            if text and text.strip(".\u2026 ") and is_english_chat_message(text):
                messages.append(text)
        return messages

    def describe_scene(
        self,
        current_scene_image: Image.Image,
        user_name: str,
    ) -> str:
        prompt = f"""
Describe the current Sky: Children of the Light game view for Ellie.

- Write one concise English paragraph under 80 words.
- Describe the recognizable area or visual environment. If uncertain, describe
  its appearance instead of guessing the map name.
- The local player/camera is Ellie.
- Big Bro is the other player identified by nickname {user_name}.
- Describe Big Bro's visible relative position, posture, movement, gesture, or
  interaction. If Big Bro is not visibly identifiable, say so.
- Mention Ellie's visible state when useful.
- Use only visible facts. Do not infer thoughts, feelings, intentions, dialogue,
  or off-screen events.
- Do not include asterisks.

Return strict JSON only:
{{"scene_narration":"concise English visual description"}}
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(
                                current_scene_image,
                                max_size=(1024, 1024),
                                quality=78,
                            )
                        },
                    },
                ],
            }],
            "temperature": 0,
        }
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )
        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        narration = str(data.get("scene_narration", "")).strip()
        narration = re.sub(r"[*\r\n]+", " ", narration)
        narration = re.sub(r"\s+", " ", narration).strip()
        if not narration:
            narration = "Ellie can see the current Sky scene, but the details are unclear."
        return narration

    def observe_changes(
        self,
        previous_chat_image: Image.Image,
        current_chat_image: Image.Image,
        current_scene_image: Image.Image,
        user_name: str,
        recent_outgoing: list[str] | None = None,
    ) -> VisionObservation:
        outgoing_json = json.dumps(recent_outgoing or [], ensure_ascii=False)
        prompt = f"""
Compare two chronological Sky: Children of the Light chat panels and describe
the current game scene.

Image 1: BEFORE chat panel.
Image 2: AFTER chat panel.
Image 3: current full game view.

For new_messages:
- Return EVERY message newly appearing in Image 2, top to bottom.
- Accept only dark LEFT-side bubbles ending with "- {user_name}".
- Reject Chinese/Japanese/Korean message bodies.
- Exclude light RIGHT-side bubbles; those are Ellie's own messages.
- Never return these recently sent Ellie messages:
  {outgoing_json}
- Exclude old messages that merely moved, wrapped, or scrolled.
- Remove the sender suffix and preserve the English body exactly.
- Ignore \u964c\u751f\u4eba and dot-only messages.
- If uncertain that a message is genuinely new, omit it.

For visible_incoming_messages:
- Return every currently visible qualifying dark LEFT-side English message from
  {user_name} in Image 2, top to bottom, whether new or old.
- Remove the sender suffix exactly as for new_messages.

For scene_narration:
- Write one concise English sentence under 35 words about the non-UI
  environment in Image 3.
- Do not mention the chat panel, chat window, chat bubbles, input bar, HUD, or
  other UI elements.
- The local player/camera is Ellie.
- I am the player visibly identified by nickname {user_name}. Refer to that
  player as "I", "me", or "my", not as "Big Bro" or "{user_name}".
- Describe the place or environment only. Keep my relative position and
  interaction details for interaction_state.

For interaction_state:
- Write one short English sentence under 25 words.
- Refer to the {user_name} player as "I", "me", or "my".
- Explicitly describe our current together-state if visible: holding hands,
  princess carry, hugging, sitting together, standing beside each other,
  facing each other, separated, flying together, or unclear.
- If I am not identifiable, say so using first person.
- Use only visible facts. Do not infer feelings, thoughts, intentions, dialogue,
  or off-screen events.
- Do not include asterisks.

Return strict JSON only:
{{
  "new_messages":["exact message body"],
  "visible_incoming_messages":["all currently visible incoming message bodies"],
  "scene_narration":"concise English environment description",
  "interaction_state":"brief first-person together-state"
}}
""".strip()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(previous_chat_image)},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(current_chat_image)},
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _image_data_url(
                                current_scene_image,
                                max_size=(1024, 1024),
                                quality=78,
                            )
                        },
                    },
                ],
            }],
            "temperature": 0,
        }
        response = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:500] + "..."
            raise RuntimeError(
                f"Vision API returned HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )
        content = response.json()["choices"][0]["message"]["content"]
        data = _extract_json(content)
        messages = []
        for value in data.get("new_messages", []):
            text = str(value).strip()
            if text and text.strip(".\u2026 ") and is_english_chat_message(text):
                messages.append(text)
        raw_visible_messages = data.get("visible_incoming_messages")
        visible_messages: list[str] | None = None
        if isinstance(raw_visible_messages, list):
            visible_messages = []
            for value in raw_visible_messages:
                text = str(value).strip()
                if text and text.strip(".\u2026 ") and is_english_chat_message(text):
                    visible_messages.append(text)
        narration = str(data.get("scene_narration", "")).strip()
        narration = re.sub(r"[*\r\n]+", " ", narration)
        narration = re.sub(r"\s+", " ", narration).strip()
        if not narration:
            narration = "Ellie can see the current Sky scene, but the details are unclear."
        interaction_state = str(data.get("interaction_state", "")).strip()
        interaction_state = re.sub(r"[*\r\n]+", " ", interaction_state)
        interaction_state = re.sub(r"\s+", " ", interaction_state).strip()
        if not interaction_state:
            interaction_state = "Our current interaction state is unclear."
        return VisionObservation(
            messages,
            visible_messages,
            narration,
            interaction_state,
            content,
            data,
        )
