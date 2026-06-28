import unittest

from ellie_sky.main import new_visible_messages
from ellie_sky.vision import (
    _normalize_visual_perspective,
    ChatMessage,
    is_supported_chat_message,
    model_request_options,
)


class MessageDiffTests(unittest.TestCase):
    def test_visual_perspective_removes_model_added_name_glosses(self):
        self.assertEqual(
            _normalize_visual_perspective(
                "她（Ellie）和我（哥哥）并肩站着。她(she)正在招手。",
                "哥哥",
            ),
            "她和我并肩站着。她正在招手。",
        )

    def test_doubao_disables_thinking(self):
        self.assertEqual(
            model_request_options("doubao-seed-1-6-vision-250815"),
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(model_request_options("openai/gpt-5.4-mini"), {})

    def test_first_scan_is_baseline(self):
        current = [ChatMessage(
            "哥哥", "Hello", "incoming", True, 0.2, "Hello - 哥哥"
        )]
        self.assertEqual(new_visible_messages([], current), [])

    def test_appended_message(self):
        old = [
            ChatMessage("哥哥", "Hello", "incoming", True, 0.2, "Hello - 哥哥"),
            ChatMessage("", "Hi", "outgoing", False, 0.8, "Hi"),
        ]
        new = [
            ChatMessage("哥哥", "Hello", "incoming", True, 0.2, "Hello - 哥哥"),
            ChatMessage("", "Hi", "outgoing", False, 0.8, "Hi"),
            ChatMessage("哥哥", "Hello", "incoming", True, 0.2, "Hello - 哥哥"),
        ]
        self.assertEqual(
            new_visible_messages(old, new),
            [ChatMessage("哥哥", "Hello", "incoming", True, 0.2, "Hello - 哥哥")],
        )

    def test_scrolled_window_overlap(self):
        old = [
            ChatMessage("哥哥", "One", "incoming", True, 0.2, "One - 哥哥"),
            ChatMessage("", "Two", "outgoing", False, 0.8, "Two"),
            ChatMessage("哥哥", "Three", "incoming", True, 0.2, "Three - 哥哥"),
        ]
        new = [
            ChatMessage("", "Two", "outgoing", False, 0.8, "Two"),
            ChatMessage("哥哥", "Three", "incoming", True, 0.2, "Three - 哥哥"),
            ChatMessage("", "Four", "outgoing", False, 0.8, "Four"),
        ]
        self.assertEqual(
            new_visible_messages(old, new),
            [ChatMessage("", "Four", "outgoing", False, 0.8, "Four")],
        )

    def test_only_left_suffix_message_is_incoming(self):
        incoming = ChatMessage(
            "哥哥", "Can you see me?", "incoming", True, 0.2,
            "Can you see me? - 哥哥",
        )
        outgoing = ChatMessage(
            "哥哥", "Hiiiiiiie!", "outgoing", False, 0.8, "Hiiiiiiie!"
        )
        missing_suffix = ChatMessage(
            "哥哥", "Maybe", "incoming", False, 0.2, "Maybe"
        )
        self.assertTrue(incoming.is_incoming_from("哥哥"))
        self.assertFalse(outgoing.is_incoming_from("哥哥"))
        self.assertFalse(missing_suffix.is_incoming_from("哥哥"))

    def test_structured_suffix_is_valid_when_vlm_removed_raw_suffix(self):
        message = ChatMessage(
            "哥哥", "Hello", "incoming", True, 0.2, "Hello"
        )
        self.assertTrue(message.is_incoming_from("哥哥"))

    def test_coordinate_jitter_does_not_create_a_new_message(self):
        old = [ChatMessage(
            "哥哥", "Hello", "incoming", True, 0.18, "Hello - 哥哥"
        )]
        new = [ChatMessage(
            "哥哥", "Hello", "incoming", True, 0.22, "Hello - 哥哥"
        )]
        self.assertEqual(new_visible_messages(old, new), [])

    def test_chinese_and_english_chat_are_accepted(self):
        self.assertTrue(is_supported_chat_message("你好，去云野吗？"))
        self.assertTrue(is_supported_chat_message("Okay，我们走吧"))
        self.assertTrue(is_supported_chat_message("Can you see me?"))
        self.assertTrue(is_supported_chat_message("hi"))
        self.assertFalse(is_supported_chat_message("F"))
        self.assertFalse(is_supported_chat_message("......"))


if __name__ == "__main__":
    unittest.main()
