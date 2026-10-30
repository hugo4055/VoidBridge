import unittest
from types import SimpleNamespace

from voice_rooms import has_human_members, room_handle_for_discord_channel, room_url


class VoiceRoomHelperTests(unittest.TestCase):
    def test_handle_is_stable_and_uses_supported_characters(self):
        channel_id = "1411504431991750718"
        handle = room_handle_for_discord_channel(channel_id)
        self.assertEqual(handle, "priv-vb-1411504431991750718")
        self.assertLessEqual(len(handle), 32)
        self.assertRegex(handle, r"^[A-Za-z0-9_-]+$")

    def test_handle_rejects_non_numeric_ids(self):
        with self.assertRaises(ValueError):
            room_handle_for_discord_channel("voice-channel")

    def test_handle_rejects_values_that_exceed_the_room_limit(self):
        with self.assertRaises(ValueError):
            room_handle_for_discord_channel("1" * 30)

    def test_room_url_validates_handle(self):
        self.assertEqual(
            room_url("priv-vb-123456789"),
            "https://golive.nemtudo.me/watch/priv-vb-123456789",
        )
        with self.assertRaises(ValueError):
            room_url("not a valid handle")

    def test_only_human_members_keep_a_call_room_active(self):
        bot_member = SimpleNamespace(bot=True)
        human_member = SimpleNamespace(bot=False)
        self.assertFalse(has_human_members([bot_member]))
        self.assertTrue(has_human_members([bot_member, human_member]))
        self.assertFalse(has_human_members([]))


if __name__ == "__main__":
    unittest.main()
