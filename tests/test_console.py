import unittest

from ellie_sky.console import banner_lines


class ConsoleTests(unittest.TestCase):
    def test_banner_contains_bunny_and_runtime_status(self):
        text = "\n".join(banner_lines(
            "LIVE CONTROL",
            "vision-model",
            True,
            "哥哥",
        ))
        self.assertIn("(\\_/)", text)
        self.assertIn("E L L I E", text)
        self.assertIn("READ-ONLY MEMORY", text)
        self.assertIn("哥哥", text)
        self.assertIn("vision-model", text)


if __name__ == "__main__":
    unittest.main()
