from odoo.tests import HttpCase, tagged

from .common import (
    LINKED_TELEGRAM_ID,
    TEST_BOT_TOKEN,
    TEST_SECRET,
    TelegramBotTestMixin,
)

UNLINKED_TELEGRAM_ID = "999888777"


@tagged("post_install", "-at_install")
class TestWebhook(TelegramBotTestMixin, HttpCase):
    _message_id = 0

    def setUp(self):
        super().setUp()
        # Bind the requests to the test database, as Telegram's are bound by the
        # server's database filter in production.
        self.authenticate(None, None)

    def _update(self, text, from_id, chat_type="private", username=None):
        """Build a Telegram update carrying a text message."""
        TestWebhook._message_id += 1
        sender = {"id": int(from_id), "is_bot": False, "first_name": "Test"}
        if username:
            sender["username"] = username
        chat_id = int(from_id) if chat_type == "private" else -100123
        return {
            "update_id": self._message_id,
            "message": {
                "message_id": self._message_id,
                "from": sender,
                "chat": {"id": chat_id, "type": chat_type},
                "date": 1700000000,
                "text": text,
            },
        }

    def _post(self, update, token=TEST_BOT_TOKEN, secret=TEST_SECRET):
        headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
        response = self.url_open(
            f"/telegram/webhook/{token}", json=update, headers=headers
        )
        self.assertEqual(response.status_code, 200)

    def _chat(self, telegram_id):
        self.env.invalidate_all()
        return self.env["telegram.chat"].search(
            [("bot_id", "=", self.bot.id), ("chat_id", "=", telegram_id)]
        )

    def _assert_ignored(self):
        self.env.invalidate_all()
        self.assertFalse(self.bot.chat_ids, "the update must not create a chat")
        self.assertFalse(
            self.env["telegram.message"].search([("bot_id", "=", self.bot.id)])
        )
        self.api_post.assert_not_called()

    # Invalid token or secret

    def test_invalid_token_is_ignored(self):
        self._post(
            self._update("/start", LINKED_TELEGRAM_ID), token="123456789:wrong-token"
        )
        self._assert_ignored()

    def test_invalid_secret_is_ignored(self):
        self._post(self._update("/start", LINKED_TELEGRAM_ID), secret="wrong-secret")
        self._assert_ignored()

    def test_missing_secret_header_is_ignored(self):
        self._post(self._update("/start", LINKED_TELEGRAM_ID), secret=None)
        self._assert_ignored()

    def test_bot_without_secret_ignores_updates(self):
        self.bot.webhook_secret = False
        self._post(self._update("/start", LINKED_TELEGRAM_ID), secret=None)
        self._assert_ignored()

    # Linked user

    def test_whoami_answers_with_odoo_user(self):
        self._post(self._update("/quiensoy", LINKED_TELEGRAM_ID))
        chat = self._chat(LINKED_TELEGRAM_ID)
        self.assertEqual(chat.partner_id, self.user.partner_id)
        [reply] = self._sent_texts()
        self.assertIn("Ana Colaboradora", reply)
        self.assertIn("ana@example.com", reply)

    def test_start_linked_user_does_not_show_id(self):
        self._post(self._update("/start", LINKED_TELEGRAM_ID))
        [reply] = self._sent_texts()
        self.assertNotIn(LINKED_TELEGRAM_ID, reply)

    def test_clearing_contact_id_unlinks_chat(self):
        self._post(self._update("/quiensoy", LINKED_TELEGRAM_ID))
        self.user.partner_id.telegram_user_id = False
        self._post(self._update("/quiensoy", LINKED_TELEGRAM_ID))
        self.assertFalse(self._chat(LINKED_TELEGRAM_ID).partner_id)

    # Unlinked user

    def test_start_unlinked_user_gets_telegram_id(self):
        self._post(self._update("/start", UNLINKED_TELEGRAM_ID))
        chat = self._chat(UNLINKED_TELEGRAM_ID)
        self.assertTrue(chat)
        self.assertFalse(chat.partner_id)
        [reply] = self._sent_texts()
        self.assertIn(UNLINKED_TELEGRAM_ID, reply)
        self.assertIn("administrador", reply)

    def test_whoami_unlinked_user_is_refused(self):
        self._post(self._update("/quiensoy", UNLINKED_TELEGRAM_ID))
        [reply] = self._sent_texts()
        self.assertIn("vinculada", reply)
        self.assertNotIn("Ana Colaboradora", reply)

    def test_username_never_links(self):
        # A sender cannot borrow a linked identity by its @username.
        self._post(
            self._update("/quiensoy", UNLINKED_TELEGRAM_ID, username=LINKED_TELEGRAM_ID)
        )
        self.assertFalse(self._chat(UNLINKED_TELEGRAM_ID).partner_id)

    def test_group_chat_is_not_linked(self):
        # In a group every member would act as the linked user.
        self._post(self._update("/quiensoy", LINKED_TELEGRAM_ID, chat_type="group"))
        chat = self._chat("-100123")
        self.assertTrue(chat)
        self.assertFalse(chat.partner_id)
        [reply] = self._sent_texts()
        self.assertNotIn("Ana Colaboradora", reply)
