import unittest

from ellie_sky.chat import (
    format_player_messages,
    is_message_directed_elsewhere,
    is_placeholder_message,
)
from ellie_sky.commands import is_interaction_command
from ellie_sky.text import build_ellie_input
from ellie_sky.vision import PlayerMessage


class ChatFormattingTests(unittest.TestCase):
    def test_dot_only_placeholder_messages_are_filtered(self):
        self.assertTrue(is_placeholder_message("..."))
        self.assertTrue(is_placeholder_message(" ……… "))
        self.assertTrue(is_placeholder_message("。。。"))
        self.assertFalse(is_placeholder_message("你好..."))
        self.assertFalse(is_placeholder_message("hi"))

    def test_primary_message_with_at_sign_is_directed_elsewhere(self):
        self.assertTrue(is_message_directed_elsewhere(
            PlayerMessage("哥哥", "@芊芊 我们走吧"),
            "哥哥",
        ))
        self.assertTrue(is_message_directed_elsewhere(
            PlayerMessage("哥哥", "＠芊芊 我们走吧"),
            "哥哥",
        ))

    def test_other_players_at_sign_is_filtered(self):
        self.assertTrue(is_message_directed_elsewhere(
            PlayerMessage("芊芊", "@哥哥 你好"),
            "哥哥",
        ))

    def test_at_sign_takes_priority_over_interaction_keyword(self):
        message = PlayerMessage("芊芊", "@哥哥 快上来")
        self.assertTrue(is_message_directed_elsewhere(message, "哥哥"))
        self.assertTrue(is_interaction_command(message.text))

    def test_primary_and_other_player_messages_are_formatted_together(self):
        messages = [
            PlayerMessage("哥哥", "你们好呀"),
            PlayerMessage("芊芊", "你好！"),
        ]
        self.assertEqual(
            format_player_messages(messages, "哥哥"),
            "你们好呀\n芊芊说：你好！",
        )

    def test_other_player_using_command_word_triggers_interaction(self):
        message = PlayerMessage("芊芊", "Ellie快上来")
        self.assertTrue(is_interaction_command(message.text))

    def test_complete_sillytavern_input_format(self):
        dialogue = format_player_messages([
            PlayerMessage("哥哥", "你好"),
            PlayerMessage("芊芊", "你们在做什么？"),
        ], "哥哥")
        self.assertEqual(
            build_ellie_input(dialogue, "我们在遇境。", "我站在她身边。"),
            "*我们在遇境。我站在她身边。*你好\n"
            "芊芊说：你们在做什么？",
        )


if __name__ == "__main__":
    unittest.main()
