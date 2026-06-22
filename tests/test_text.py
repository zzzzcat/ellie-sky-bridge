import unittest

from ellie_sky.text import (
    build_ellie_input,
    game_speech_chunks,
    split_ellie_output,
    split_for_game,
)


class TextTests(unittest.TestCase):
    def test_interleaved_actions_and_speech(self):
        text = "*smiles softly*\nThank you, Big Bro...\n*holds his hand*\nI am ready."
        speech, actions = split_ellie_output(text)
        self.assertEqual(speech, ["Thank you, Big Bro...", "I am ready."])
        self.assertEqual(actions, ["smiles softly", "holds his hand"])

    def test_game_limit(self):
        chunks = split_for_game("one two three four five", 10)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))
        self.assertEqual(" ".join(chunks), "one two three four five")

    def test_spoken_sections_are_kept_separate(self):
        chunks = game_speech_chunks(
            ["Hiiiiiiie, Big Bro!", "Ellie is all ready to play!"],
            388,
        )
        self.assertEqual(
            chunks,
            ["Hiiiiiiie, Big Bro!", "Ellie is all ready to play!"],
        )

    def test_long_spoken_section_is_split(self):
        chunks = game_speech_chunks(["one two three four five"], 10)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))
        self.assertEqual(" ".join(chunks), "one two three four five")

    def test_visual_narration_is_wrapped_in_single_asterisks(self):
        value = build_ellie_input(
            "Can you see me?",
            "I am standing beside Ellie and waving.",
            "I am standing beside Ellie.",
        )
        self.assertEqual(
            value,
            "*I am standing beside Ellie and waving. I am standing beside Ellie.*\nCan you see me?",
        )

    def test_visual_narration_cannot_break_asterisk_wrapper(self):
        value = build_ellie_input("Hello", "*I wave beside Ellie.*", "*We stand close.*")
        self.assertEqual(value, "*I wave beside Ellie. We stand close.*\nHello")

    def test_scene_can_be_omitted_when_unchanged(self):
        value = build_ellie_input(
            "Still here?",
            "A stone room surrounds us.",
            "I am standing beside Ellie.",
            include_scene=False,
        )
        self.assertEqual(value, "*I am standing beside Ellie.*\nStill here?")


if __name__ == "__main__":
    unittest.main()
