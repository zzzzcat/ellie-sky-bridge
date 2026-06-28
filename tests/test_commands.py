import unittest

from ellie_sky.commands import is_interaction_command


class CommandTests(unittest.TestCase):
    def test_interaction_commands(self):
        self.assertTrue(is_interaction_command("牵我"))
        self.assertTrue(is_interaction_command(" 上来 "))
        self.assertTrue(is_interaction_command("来牵我"))
        self.assertTrue(is_interaction_command("你快上来吧"))
        self.assertFalse(is_interaction_command("/interact"))
        self.assertFalse(is_interaction_command("/i"))
        self.assertFalse(is_interaction_command("Hi Ellie"))


if __name__ == "__main__":
    unittest.main()
