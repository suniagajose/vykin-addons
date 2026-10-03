from unittest.mock import Mock, patch

from odoo.tests import new_test_user

TEST_BOT_TOKEN = "123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
TEST_SECRET = "test-secret_123"
LINKED_TELEGRAM_ID = "111222333"


class TelegramBotTestMixin:
    """Bot, linked internal user and a mocked Telegram Bot API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bot = cls.env["telegram.bot"].create(
            {
                "name": "Vykin Bot",
                "bot_token": TEST_BOT_TOKEN,
                "webhook_secret": TEST_SECRET,
            }
        )
        cls.user = new_test_user(
            cls.env,
            login="ana@example.com",
            name="Ana Colaboradora",
            groups="base.group_user",
        )
        cls.user.partner_id.telegram_user_id = LINKED_TELEGRAM_ID

    def setUp(self):
        super().setUp()
        # Every Bot API call goes through requests.post: capture it. Each answer
        # carries a new message_id, as Telegram's do within a chat.
        message_ids = iter(range(1000, 2000))
        patcher = patch(
            "odoo.addons.telegram_bot.tools.telegram_client.requests.post",
            side_effect=lambda *args, **kwargs: Mock(
                json=Mock(
                    return_value={
                        "ok": True,
                        "result": {"message_id": next(message_ids)},
                    }
                )
            ),
        )
        self.api_post = patcher.start()
        self.addCleanup(patcher.stop)

    def _sent_texts(self):
        """Return the texts sent with sendMessage, in order."""
        return [
            call.kwargs["json"]["text"]
            for call in self.api_post.call_args_list
            if call.args[0].endswith("/sendMessage")
        ]
