import os
import unittest


os.environ.setdefault("DATABASE_URL", "postgresql://voidbridge:test@localhost:5432/voidbridge")
os.environ.setdefault("DISCORD_TOKEN", "test-discord-token")
os.environ.setdefault("GOLIVE_TOKEN", "Bot test-golive-token")

import main


class BridgeConfigViewTests(unittest.TestCase):
    def test_config_view_has_toggle_and_text_channel_selector(self):
        view = main.BridgeConfigView(
            "1411504431991750718",
            {
                "voice_rooms_enabled": True,
                "voice_announce_channel_id": None,
            },
        )
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.toggle_button.label, "Desativar salas automáticas")
        self.assertTrue(callable(view.toggle_button.callback))
        self.assertTrue(callable(view.channel_select.callback))


if __name__ == "__main__":
    unittest.main()
