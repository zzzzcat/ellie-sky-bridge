import unittest

from ellie_sky.relationship import RelationshipTracker
from ellie_sky.vision import VisionObservation


def observation(state: str, confidence: float, social_context: str = ""):
    return VisionObservation(
        new_messages=[],
        visible_incoming_messages=None,
        scene_narration="",
        interaction_state=social_context,
        relationship_state=state,
        relationship_confidence=confidence,
        relationship_evidence="test evidence",
        social_context=social_context,
    )


class RelationshipTrackerTests(unittest.TestCase):
    def test_confident_piggyback_is_accepted(self):
        tracker = RelationshipTracker()
        result = tracker.stabilize(observation("i_carry_her", 0.95))
        self.assertEqual(result.relationship_state, "i_carry_her")
        self.assertEqual(result.interaction_state, "我正背着她。")

    def test_unclear_frame_does_not_erase_piggyback(self):
        tracker = RelationshipTracker()
        tracker.stabilize(observation("i_carry_her", 0.95))
        result = tracker.stabilize(observation("unclear", 0.2))
        self.assertEqual(result.relationship_state, "i_carry_her")
        self.assertEqual(result.interaction_state, "我正背着她。")

    def test_active_state_requires_two_clear_release_frames(self):
        tracker = RelationshipTracker()
        tracker.stabilize(observation("i_carry_her", 0.95))
        first = tracker.stabilize(observation("standing_nearby", 0.9))
        second = tracker.stabilize(observation("standing_nearby", 0.9))
        self.assertEqual(first.relationship_state, "i_carry_her")
        self.assertEqual(second.relationship_state, "standing_nearby")

    def test_social_context_is_appended_after_relationship(self):
        tracker = RelationshipTracker()
        result = tracker.stabilize(observation(
            "holding_hands",
            0.9,
            "丫丫穿着蓝斗篷来到我们身边。",
        ))
        self.assertEqual(
            result.interaction_state,
            "我正牵着她的手。丫丫穿着蓝斗篷来到我们身边。",
        )


if __name__ == "__main__":
    unittest.main()
