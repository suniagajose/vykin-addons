import logging
from unittest.mock import Mock

import requests

from odoo.exceptions import AccessError, UserError
from odoo.models import get_public_method
from odoo.tests import TransactionCase, new_test_user

from ..tools import TelegramApiError, TelegramClient, TelegramTokenFilter
from .common import TEST_BOT_TOKEN, TelegramBotTestMixin


class TestTelegramBot(TelegramBotTestMixin, TransactionCase):
    def test_register_webhook_sends_url_and_secret(self):
        self.bot.webhook_secret = False
        self.bot.action_register_webhook()
        secret = self.bot.webhook_secret
        self.assertTrue(secret, "a secret is generated when empty")
        self.api_post.assert_called_once()
        url = self.api_post.call_args.args[0]
        payload = self.api_post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/setWebhook"))
        self.assertEqual(
            payload["url"],
            f"{self.bot.get_base_url()}/telegram/webhook/{TEST_BOT_TOKEN}",
        )
        self.assertEqual(payload["secret_token"], secret)

    def test_register_webhook_keeps_existing_secret(self):
        secret = self.bot.webhook_secret
        self.bot.action_register_webhook()
        self.assertEqual(self.api_post.call_args.kwargs["json"]["secret_token"], secret)

    def test_register_webhook_rejected(self):
        self.api_post.side_effect = None
        self.api_post.return_value = Mock(
            json=lambda: {"ok": False, "description": "Bad Request: bad webhook"}
        )
        with self.assertRaisesRegex(UserError, "bad webhook"):
            self.bot.action_register_webhook()

    def test_token_hash_follows_token(self):
        self.assertEqual(
            self.env["telegram.bot"].find_by_token(TEST_BOT_TOKEN), self.bot
        )
        new_token = "987654321:ZYX-DEF1234ghIkl-zyx57W2v1u123ew11"
        self.bot.bot_token = new_token
        self.assertFalse(self.env["telegram.bot"].find_by_token(TEST_BOT_TOKEN))
        self.assertEqual(self.env["telegram.bot"].find_by_token(new_token), self.bot)

    def test_secrets_restricted_to_administrators(self):
        user = new_test_user(self.env, login="employee", groups="base.group_user")
        bot = self.bot.with_user(user)
        self.assertEqual(bot.name, "Vykin Bot")
        for field in ("bot_token", "webhook_secret"):
            with self.assertRaises(AccessError):
                bot.read([field])
        with self.assertRaises(AccessError):
            self.user.partner_id.with_user(user).write({"telegram_user_id": "123"})

    def test_helpers_not_callable_over_rpc(self):
        # They run with sudo, so over RPC any user could message any chat or
        # rewrite any conversation as the bot.
        for model, names in {
            "telegram.bot": (
                "find_by_token",
                "get_file_content",
                "get_markdown_helpers",
                "send_document",
                "send_message",
                "answer_callback_query",
                "edit_message_reply_markup",
            ),
            "telegram.chat": (
                "increment_failed_state",
                "reset_failed_state",
                "transition_to",
                "get_context",
                "set_context",
                "reset_conversation",
                "clear_context",
            ),
        }.items():
            for name in names:
                with self.subTest(method=f"{model}.{name}"):
                    with self.assertRaises(AccessError):
                        get_public_method(self.env[model], name)

    def test_register_webhook_restricted_to_administrators(self):
        user = new_test_user(self.env, login="employee", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.bot.with_user(user).action_register_webhook()
        self.api_post.assert_not_called()

    def test_client_error_does_not_leak_token(self):
        self.api_post.side_effect = requests.ConnectionError(
            f"Max retries exceeded with url: /bot{TEST_BOT_TOKEN}/sendMessage"
        )
        with self.assertLogs("odoo.addons.telegram_bot", level="ERROR") as logs:
            with self.assertRaises(TelegramApiError) as error:
                TelegramClient(TEST_BOT_TOKEN).send_message("1", "hola")
        self.assertNotIn(TEST_BOT_TOKEN, str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertNotIn(TEST_BOT_TOKEN, "\n".join(logs.output))

    def test_log_filter_masks_token(self):
        access_logger = logging.getLogger("odoo.http.server")
        self.assertTrue(
            any(isinstance(f, TelegramTokenFilter) for f in access_logger.filters),
            "the HTTP access log must be filtered",
        )
        record = logging.LogRecord(
            "odoo.http.server",
            logging.INFO,
            __file__,
            1,
            "POST /telegram/webhook/%s HTTP/1.1",
            (TEST_BOT_TOKEN,),
            None,
        )
        # The access log also carries the line in colored_message, which
        # Odoo's formatter prints instead of the message.
        record.colored_message = f"POST /telegram/webhook/{TEST_BOT_TOKEN} HTTP/1.1"
        TelegramTokenFilter().filter(record)
        self.assertNotIn(TEST_BOT_TOKEN, record.getMessage())
        self.assertIn("123456789:***...ew11", record.getMessage())
        self.assertNotIn(TEST_BOT_TOKEN, record.colored_message)
