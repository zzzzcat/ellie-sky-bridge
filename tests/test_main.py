import unittest

from ellie_sky.main import new_visible_messages
from ellie_sky.vision import ChatMessage, is_english_chat_message


class MessageDiffTests(unittest.TestCase):
    def test_first_scan_is_baseline(self):
        current = [ChatMessage(
            "Big_Bro", "Hello", "incoming", True, 0.2, "Hello - Big_Bro"
        )]
        self.assertEqual(new_visible_messages([], current), [])

    def test_appended_message(self):
        old = [
            ChatMessage("Big_Bro", "Hello", "incoming", True, 0.2, "Hello - Big_Bro"),
            ChatMessage("", "Hi", "outgoing", False, 0.8, "Hi"),
        ]
        new = [
            ChatMessage("Big_Bro", "Hello", "incoming", True, 0.2, "Hello - Big_Bro"),
            ChatMessage("", "Hi", "outgoing", False, 0.8, "Hi"),
            ChatMessage("Big_Bro", "Hello", "incoming", True, 0.2, "Hello - Big_Bro"),
        ]
        self.assertEqual(
            new_visible_messages(old, new),
            [ChatMessage("Big_Bro", "Hello", "incoming", True, 0.2, "Hello - Big_Bro")],
        )

    def test_scrolled_window_overlap(self):
        old = [
            ChatMessage("Big_Bro", "One", "incoming", True, 0.2, "One - Big_Bro"),
            ChatMessage("", "Two", "outgoing", False, 0.8, "Two"),
            ChatMessage("Big_Bro", "Three", "incoming", True, 0.2, "Three - Big_Bro"),
        ]
        new = [
            ChatMessage("", "Two", "outgoing", False, 0.8, "Two"),
            ChatMessage("Big_Bro", "Three", "incoming", True, 0.2, "Three - Big_Bro"),
            ChatMessage("", "Four", "outgoing", False, 0.8, "Four"),
        ]
        self.assertEqual(
            new_visible_messages(old, new),
            [ChatMessage("", "Four", "outgoing", False, 0.8, "Four")],
        )

    def test_only_left_suffix_message_is_incoming(self):
        incoming = ChatMessage(
            "Big_Bro", "Can you see me?", "incoming", True, 0.2,
            "Can you see me? - Big_Bro",
        )
        outgoing = ChatMessage(
            "Big_Bro", "Hiiiiiiie!", "outgoing", False, 0.8, "Hiiiiiiie!"
        )
        missing_suffix = ChatMessage(
            "Big_Bro", "Maybe", "incoming", False, 0.2, "Maybe"
        )
        self.assertTrue(incoming.is_incoming_from("Big_Bro"))
        self.assertFalse(outgoing.is_incoming_from("Big_Bro"))
        self.assertFalse(missing_suffix.is_incoming_from("Big_Bro"))

    def test_structured_suffix_is_valid_when_vlm_removed_raw_suffix(self):
        message = ChatMessage(
            "Big_Bro", "Hello", "incoming", True, 0.2, "Hello"
        )
        self.assertTrue(message.is_incoming_from("Big_Bro"))

    def test_coordinate_jitter_does_not_create_a_new_message(self):
        old = [ChatMessage(
            "Big_Bro", "Hello", "incoming", True, 0.18, "Hello - Big_Bro"
        )]
        new = [ChatMessage(
            "Big_Bro", "Hello", "incoming", True, 0.22, "Hello - Big_Bro"
        )]
        self.assertEqual(new_visible_messages(old, new), [])

    def test_only_english_chat_is_accepted(self):
        self.assertTrue(is_english_chat_message("Can you see me?"))
        self.assertTrue(is_english_chat_message("Hi, Big Bro!"))
        self.assertFalse(is_english_chat_message("你好，去云野吗？"))
        self.assertFalse(is_english_chat_message("Okay，我们走吧"))


if __name__ == "__main__":
    unittest.main()
