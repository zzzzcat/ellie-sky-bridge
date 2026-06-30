from __future__ import annotations

from dataclasses import replace

from .vision import VisionObservation


RELATIONSHIP_TEXT = {
    "i_carry_her": "我正背着她。",
    "she_carries_me": "她正背着我。",
    "holding_hands": "我正牵着她的手。",
    "i_princess_carry_her": "我正公主抱着她。",
    "she_princess_carries_me": "她正公主抱着我。",
    "hugging": "我和她正拥抱着。",
    "sitting_together": "我和她正坐在一起。",
    "standing_nearby": "我和她站在彼此身边。",
    "separated": "我和她目前分开着。",
    "unclear": "我和她目前的互动状态看不清。",
}

ACTIVE_RELATIONSHIPS = {
    "i_carry_her",
    "she_carries_me",
    "holding_hands",
    "i_princess_carry_her",
    "she_princess_carries_me",
    "hugging",
    "sitting_together",
}


class RelationshipTracker:
    """Stabilize persistent character interactions across occluded frames."""

    def __init__(self):
        self.current_state = "unclear"
        self.current_confidence = 0.0
        self.pending_state = ""
        self.pending_count = 0

    def stabilize(self, observation: VisionObservation) -> VisionObservation:
        candidate = observation.relationship_state
        confidence = observation.relationship_confidence

        if candidate == self.current_state:
            self.current_confidence = confidence
            self._clear_pending()
        elif candidate == "unclear":
            self._clear_pending()
        elif self.current_state == "unclear":
            if confidence >= 0.65:
                self._accept(candidate, confidence)
        elif self.current_state in ACTIVE_RELATIONSHIPS:
            if candidate in ACTIVE_RELATIONSHIPS and confidence >= 0.8:
                self._accept(candidate, confidence)
            elif confidence >= 0.75:
                self._consider_transition(candidate, confidence, required_frames=2)
        elif confidence >= 0.65:
            self._accept(candidate, confidence)

        relationship_text = RELATIONSHIP_TEXT[self.current_state]
        social_context = observation.social_context.strip()
        interaction_state = relationship_text
        if social_context:
            interaction_state += social_context
        return replace(
            observation,
            interaction_state=interaction_state,
            relationship_state=self.current_state,
            relationship_confidence=self.current_confidence,
        )

    def _consider_transition(
        self,
        candidate: str,
        confidence: float,
        required_frames: int,
    ) -> None:
        if candidate == self.pending_state:
            self.pending_count += 1
        else:
            self.pending_state = candidate
            self.pending_count = 1
        if self.pending_count >= required_frames:
            self._accept(candidate, confidence)

    def _accept(self, state: str, confidence: float) -> None:
        self.current_state = state
        self.current_confidence = confidence
        self._clear_pending()

    def _clear_pending(self) -> None:
        self.pending_state = ""
        self.pending_count = 0
