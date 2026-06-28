import unittest

from ellie_sky.memory_chat import (
    MemoryChatPacket,
    extract_chat_packets,
    extract_friend_names,
    resolve_packet,
)


BROTHER_ID = "9c76cc6e-8e93-4159-92ae-754ea80018c0"
ELLIE_ID = "d3e630ec-317c-45da-a08f-ef4aaabcf21a"


class MemoryChatTests(unittest.TestCase):
    def test_extracts_incoming_chat_packet(self):
        data = (
            b'noise{"type":"chat","sig":"xxx_sig","sender_id":"'
            + BROTHER_ID.encode()
            + b'","msg":"Ellie\xe6\x88\x91\xe6\x98\xaf\xe5\x93\xa5\xe5\x93\xa5","ch":"local"}tail'
        )
        self.assertEqual(
            extract_chat_packets(data),
            [MemoryChatPacket(BROTHER_ID, "Ellie我是哥哥")],
        )

    def test_extracts_local_success_packet(self):
        data = (
            b'{"type":"chat","sender_id":"' + ELLIE_ID.encode()
            + b'","result":"success","msg_id":"m1",'
            + b'"msg":"local test","ch":"local"}'
        )
        self.assertEqual(
            extract_chat_packets(data),
            [MemoryChatPacket(ELLIE_ID, "local test", "m1", "success")],
        )

    def test_extracts_friend_id_to_nickname_mapping(self):
        data = (
            b'{"set_friends":[{"friend_id":"' + BROTHER_ID.encode()
            + b'","nickname":"\xe5\x93\xa5\xe5\x93\xa5","abilities":[]}]}'
        )
        self.assertEqual(extract_friend_names(data), {BROTHER_ID: "哥哥"})

    def test_resolve_filters_local_and_names_primary_user(self):
        local = MemoryChatPacket(ELLIE_ID, "hello", "m1", "success")
        self.assertIsNone(resolve_packet(local, ELLIE_ID, BROTHER_ID, "哥哥", {}))
        incoming = MemoryChatPacket(BROTHER_ID, "你好")
        event = resolve_packet(incoming, ELLIE_ID, BROTHER_ID, "哥哥", {})
        self.assertIsNotNone(event)
        self.assertEqual(event.sender, "哥哥")
        self.assertEqual(event.text, "你好")

    def test_resolve_uses_friend_nickname(self):
        friend_id = "11111111-2222-3333-4444-555555555555"
        packet = MemoryChatPacket(friend_id, "你好")
        event = resolve_packet(
            packet,
            ELLIE_ID,
            BROTHER_ID,
            "哥哥",
            {friend_id: "芊芊"},
        )
        self.assertEqual(event.sender, "芊芊")

    def test_resolve_filters_unknown_non_friend(self):
        stranger_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        packet = MemoryChatPacket(stranger_id, "陌生人坐在旁边说话")
        self.assertIsNone(resolve_packet(
            packet,
            ELLIE_ID,
            BROTHER_ID,
            "哥哥",
            {},
        ))


if __name__ == "__main__":
    unittest.main()
