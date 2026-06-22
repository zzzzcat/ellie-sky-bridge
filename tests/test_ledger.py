import unittest

from ellie_sky.ledger import IncomingLedger, MessageLedger, normalize_chat_text


class LedgerTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(
            normalize_chat_text("Hiiiiiiie, Big Bro!"),
            "hiiiiiiie big bro",
        )

    def test_exact_outgoing_echo_is_suppressed(self):
        ledger = MessageLedger()
        ledger.record_outgoing("Hey hubby! What's on your mind?")
        self.assertTrue(
            ledger.is_outgoing_echo("Hey hubby! What's on your mind?")
        )

    def test_minor_ocr_difference_is_suppressed(self):
        ledger = MessageLedger()
        ledger.record_outgoing("Hiiiiiiie, Big Bro!")
        self.assertTrue(ledger.is_outgoing_echo("Hiiiiiiie Big Bro"))

    def test_unrelated_big_bro_message_is_not_suppressed(self):
        ledger = MessageLedger()
        ledger.record_outgoing("Hiiiiiiie, Big Bro!")
        self.assertFalse(ledger.is_outgoing_echo("Can you see me?"))

    def test_duplicate_incoming_is_suppressed(self):
        ledger = IncomingLedger(duplicate_window_seconds=45)
        self.assertTrue(ledger.should_process("Can you see me?"))
        self.assertFalse(ledger.should_process("Can you see me?"))

    def test_incoming_normalization_suppresses_punctuation_drift(self):
        ledger = IncomingLedger(duplicate_window_seconds=45)
        self.assertTrue(ledger.should_process("Hello, Ellie!"))
        self.assertFalse(ledger.should_process("Hello Ellie"))

    def test_different_incoming_is_processed(self):
        ledger = IncomingLedger(duplicate_window_seconds=45)
        self.assertTrue(ledger.should_process("Can you see me?"))
        self.assertTrue(ledger.should_process("Where are you?"))

    def test_visible_bubble_stays_suppressed_without_time_limit(self):
        ledger = IncomingLedger()
        visible = ["Can you see me?"]
        self.assertTrue(ledger.should_process("Can you see me?", visible))
        self.assertFalse(ledger.should_process("Can you see me?", visible))

    def test_same_text_is_suppressed_after_vlm_temporarily_loses_bubble(self):
        ledger = IncomingLedger()
        visible = ["Can you see me?"]
        self.assertTrue(ledger.should_process("Can you see me?", visible))
        ledger.reconcile_visible([])
        self.assertFalse(ledger.should_process("Can you see me?", visible))

    def test_two_visible_identical_bubbles_are_two_events(self):
        ledger = IncomingLedger()
        visible = ["Hello", "Hello"]
        self.assertTrue(ledger.should_process("Hello", visible))
        self.assertTrue(ledger.should_process("Hello", visible))
        self.assertFalse(ledger.should_process("Hello", visible))

    def test_empty_visible_list_does_not_release_old_bubbles(self):
        ledger = IncomingLedger()
        self.assertTrue(ledger.should_process("Hello", ["Hello"]))
        self.assertFalse(ledger.should_process("Hello", ["Hello"]))
        ledger.reconcile_visible([])
        self.assertFalse(ledger.should_process("Hello", ["Hello"]))

    def test_missing_visible_list_uses_time_window_fallback(self):
        ledger = IncomingLedger(duplicate_window_seconds=45)
        self.assertTrue(ledger.should_process("Hello", None))
        self.assertFalse(ledger.should_process("Hello", None))

    def test_visible_omission_then_old_message_reappears_is_suppressed(self):
        ledger = IncomingLedger()
        self.assertTrue(ledger.should_process("What do you wanna do?", [
            "What do you wanna do?",
        ]))
        ledger.reconcile_visible([])
        self.assertFalse(ledger.should_process("What do you wanna do?", [
            "What do you wanna do?",
            "New real message",
        ]))
        self.assertTrue(ledger.should_process("New real message", [
            "What do you wanna do?",
            "New real message",
        ]))


if __name__ == "__main__":
    unittest.main()
