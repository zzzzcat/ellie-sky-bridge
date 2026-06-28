from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher


def normalize_chat_text(text: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else " "
        for character in text
    )
    return re.sub(r"\s+", " ", normalized).strip()


@dataclass(frozen=True)
class OutgoingMessage:
    text: str
    sent_at: float


class MessageLedger:
    def __init__(self, ttl_seconds: float = 600.0, max_messages: int = 50):
        self.ttl_seconds = ttl_seconds
        self.messages: deque[OutgoingMessage] = deque(maxlen=max_messages)

    def record_outgoing(self, text: str) -> None:
        self.messages.append(OutgoingMessage(text=text, sent_at=time.monotonic()))

    def recent_outgoing(self) -> list[str]:
        self._prune()
        return [item.text for item in self.messages]

    def is_outgoing_echo(self, text: str) -> bool:
        self._prune()
        candidate = normalize_chat_text(text)
        if not candidate:
            return False
        for item in self.messages:
            known = normalize_chat_text(item.text)
            if candidate == known:
                return True
            if min(len(candidate), len(known)) >= 8:
                if SequenceMatcher(None, candidate, known).ratio() >= 0.88:
                    return True
        return False

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        while self.messages and self.messages[0].sent_at < cutoff:
            self.messages.popleft()


class IncomingLedger:
    """Track processed incoming message text despite noisy VLM visibility."""

    def __init__(
        self,
        duplicate_window_seconds: float = 45.0,
        max_messages: int = 500,
        processed_ttl_seconds: float = 1800.0,
    ):
        self.duplicate_window_seconds = duplicate_window_seconds
        self.processed_ttl_seconds = processed_ttl_seconds
        self.recent: deque[OutgoingMessage] = deque(maxlen=max_messages)
        self.processed: deque[OutgoingMessage] = deque(maxlen=max_messages)

    def reconcile_visible(self, visible_messages: list[str]) -> None:
        # Visibility is useful as a count hint, but not reliable enough to
        # release processed messages. The VLM sometimes omits old bubbles or
        # misclassifies Ellie's right-side messages as visible incoming text.
        self._prune_processed()

    def should_process(self, text: str, visible_messages: list[str] | None = None) -> bool:
        candidate = normalize_chat_text(text)
        if not candidate:
            return False
        self._prune_processed()

        visible_count = 1
        if visible_messages is not None:
            visible_count = sum(
                1 for item in visible_messages
                if normalize_chat_text(item) == candidate
            )
            visible_count = max(1, visible_count)
            processed_count = sum(
                1 for item in self.processed
                if normalize_chat_text(item.text) == candidate
            )
            if processed_count >= visible_count:
                return False
            self.processed.append(OutgoingMessage(text=text, sent_at=time.monotonic()))
            return True

        if visible_messages is None:
            now = time.monotonic()
            cutoff = now - self.duplicate_window_seconds
            while self.recent and self.recent[0].sent_at < cutoff:
                self.recent.popleft()
            if self._processed_count(candidate) > 0:
                return False
            if any(normalize_chat_text(item.text) == candidate for item in self.recent):
                return False
            self.recent.append(OutgoingMessage(text=text, sent_at=now))
            self.processed.append(OutgoingMessage(text=text, sent_at=now))
            return True

    def _processed_count(self, normalized_text: str) -> int:
        return sum(
            1 for item in self.processed
            if normalize_chat_text(item.text) == normalized_text
        )

    def _prune_processed(self) -> None:
        cutoff = time.monotonic() - self.processed_ttl_seconds
        while self.processed and self.processed[0].sent_at < cutoff:
            self.processed.popleft()
