import unittest

from ellie_sky.interaction import InteractionController, active_together_state
from ellie_sky.vision import VisionObservation


class InteractionTests(unittest.TestCase):
    def test_chinese_active_together_state_is_detected(self):
        self.assertTrue(active_together_state("我正在背着她。"))
        self.assertTrue(active_together_state("我和她正牵着手。"))

    def test_active_together_state_blocks_repeated_f_press(self):
        self.assertTrue(active_together_state("I am carrying her."))
        self.assertTrue(active_together_state("We are holding hands."))
        self.assertFalse(active_together_state("I am standing beside her."))

    def test_non_friend_tree_prompt_is_pressed(self):
        observation = VisionObservation(
            new_messages=[],
            visible_incoming_messages=[],
            scene_narration="",
            interaction_state="I am standing beside her.",
            f_prompt_visible=True,
            is_friend_tree_star=False,
            interaction_confidence=0.95,
        )
        decision = InteractionController().decide(
            observation,
            cooldown_ready=True,
            request_active=True,
        )
        self.assertTrue(decision.should_press)
        self.assertEqual(decision.reason, "press_non_friend_tree_prompt")

    def test_non_friend_tree_prompt_is_skipped_without_request(self):
        observation = VisionObservation(
            new_messages=[],
            visible_incoming_messages=[],
            scene_narration="",
            interaction_state="I am standing beside her.",
            f_prompt_visible=True,
            is_friend_tree_star=False,
            interaction_confidence=0.95,
        )
        decision = InteractionController().decide(
            observation,
            cooldown_ready=True,
            request_active=False,
        )
        self.assertFalse(decision.should_press)
        self.assertEqual(decision.reason, "interaction_not_requested")

    def test_friend_tree_panel_is_closed_before_f_prompt_handling(self):
        observation = VisionObservation(
            new_messages=[],
            visible_incoming_messages=[],
            scene_narration="",
            interaction_state="I am standing beside her.",
            friend_tree_panel_open=True,
            f_prompt_visible=True,
            is_friend_tree_star=False,
            interaction_confidence=0.98,
        )
        decision = InteractionController().decide(
            observation,
            cooldown_ready=True,
            request_active=False,
        )
        self.assertTrue(decision.should_press)
        self.assertEqual(decision.reason, "close_friend_tree_panel")

    def test_friend_tree_prompt_is_skipped(self):
        observation = VisionObservation(
            new_messages=[],
            visible_incoming_messages=[],
            scene_narration="",
            interaction_state="I am standing beside her.",
            f_prompt_visible=True,
            is_friend_tree_star=True,
            interaction_confidence=0.95,
        )
        decision = InteractionController().decide(
            observation,
            cooldown_ready=True,
            request_active=True,
        )
        self.assertFalse(decision.should_press)
        self.assertEqual(decision.reason, "friend_tree_star")

    def test_active_together_state_is_skipped(self):
        observation = VisionObservation(
            new_messages=[],
            visible_incoming_messages=[],
            scene_narration="",
            interaction_state="I am carrying her.",
            f_prompt_visible=True,
            is_friend_tree_star=False,
            interaction_confidence=0.95,
        )
        decision = InteractionController().decide(
            observation,
            cooldown_ready=True,
            request_active=True,
        )
        self.assertFalse(decision.should_press)
        self.assertEqual(decision.reason, "active_together_state")


if __name__ == "__main__":
    unittest.main()
