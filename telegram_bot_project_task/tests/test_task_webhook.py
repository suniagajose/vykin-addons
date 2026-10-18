from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.telegram_bot.tests.common import (
    LINKED_TELEGRAM_ID,
    TEST_BOT_TOKEN,
    TEST_SECRET,
    TelegramBotTestMixin,
)


@tagged("post_install", "-at_install")
class TestTaskWebhook(TelegramBotTestMixin, HttpCase):
    """The task flow through the real webhook, as Telegram drives it.

    The webhook runs as the public user: unlike a direct controller call, the
    transaction's origin user is public, which Odoo falls back to when it
    writes relations to res.users (``_allow_sudo_commands``).
    """

    _update_id = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user.group_ids |= cls.env.ref("project.group_project_user")
        cls.project = cls.env["project.project"].create(
            {"name": "Soporte", "privacy_visibility": "employees"}
        )
        cls.bot.write(
            {
                "command_ids": [
                    (4, cls.env.ref("telegram_bot_project_task.command_nueva").id)
                ],
                "project_task_project_id": cls.project.id,
            }
        )

    def setUp(self):
        super().setUp()
        # Bind the requests to the test database, as in telegram_bot's tests.
        self.authenticate(None, None)
        patcher = patch.object(
            type(self.env["telegram.bot"]),
            "_ai_chat_json",
            autospec=True,
            return_value={"title": "Llamar al cliente", "description": "Factura."},
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def _post(self, payload):
        TestTaskWebhook._update_id += 1
        response = self.url_open(
            f"/telegram/webhook/{TEST_BOT_TOKEN}",
            json={"update_id": self._update_id, **payload},
            headers={"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET},
        )
        self.assertEqual(response.status_code, 200)

    def _sender(self):
        return {"id": int(LINKED_TELEGRAM_ID), "is_bot": False, "first_name": "T"}

    def _chat_data(self):
        return {"id": int(LINKED_TELEGRAM_ID), "type": "private"}

    def _text(self, text):
        self._post(
            {
                "message": {
                    "message_id": self._update_id + 1,
                    "from": self._sender(),
                    "chat": self._chat_data(),
                    "date": 1700000000,
                    "text": text,
                }
            }
        )

    def _press(self, action):
        self._post(
            {
                "callback_query": {
                    "id": f"cb-{self._update_id}",
                    "from": self._sender(),
                    "message": {"message_id": 1, "chat": self._chat_data()},
                    "data": f"tk:{action}",
                }
            }
        )

    def test_create_through_webhook_acts_as_linked_user(self):
        self._text("Llamar al cliente por la factura")
        self._press("continue")
        self._press("create")

        self.env.invalidate_all()
        task = self.env["project.task"].search([("project_id", "=", self.project.id)])
        self.assertEqual(len(task), 1, self._sent_texts()[-1])
        self.assertEqual(task.create_uid, self.user)
        self.assertEqual(task.user_ids, self.user)
        self.assertIn("Tarea creada", self._sent_texts()[-1])
