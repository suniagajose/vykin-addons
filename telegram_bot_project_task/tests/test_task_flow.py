from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user

from odoo.addons.telegram_bot.tests.common import (
    LINKED_TELEGRAM_ID,
    TelegramBotTestMixin,
)

from ..controllers.project_task_controller import ProjectTaskTelegramController

UNLINKED_TELEGRAM_ID = "999888777"
PORTAL_TELEGRAM_ID = "444555666"


class TestTaskFlow(TelegramBotTestMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user.group_ids |= cls.env.ref("project.group_project_user")
        cls.project = cls.env["project.project"].create(
            {"name": "Soporte", "privacy_visibility": "employees"}
        )
        cls.private_project = cls.env["project.project"].create(
            {"name": "Dirección", "privacy_visibility": "followers"}
        )
        cls.bot.write(
            {
                "command_ids": [
                    (4, cls.env.ref("telegram_bot_project_task.command_nueva").id)
                ],
                "project_task_glossary": "Odoo, Vykin",
            }
        )
        portal = new_test_user(
            cls.env, login="portal@example.com", groups="base.group_portal"
        )
        portal.partner_id.telegram_user_id = PORTAL_TELEGRAM_ID
        cls.controller = ProjectTaskTelegramController()

    def setUp(self):
        super().setUp()
        self._message_id = 0
        bot_class = type(self.env["telegram.bot"])
        self.transcribe = self._patch(bot_class, "_ai_transcribe", "dictado")
        self.chat_json = self._patch(
            bot_class,
            "_ai_chat_json",
            {"title": "Revisar facturas", "description": "Revisar las de octubre."},
        )
        self._patch(bot_class, "get_file_content", b"\xff\xd8\xffcontent")

    def _patch(self, cls, name, return_value):
        patcher = patch.object(cls, name, autospec=True, return_value=return_value)
        self.addCleanup(patcher.stop)
        return patcher.start()

    # Updates, as the webhook hands them to the controller (superuser bot).

    def _message(self, from_id=LINKED_TELEGRAM_ID, **content):
        self._message_id += 1
        self.controller._dispatch_message(
            self.bot.sudo(),
            {
                "message_id": self._message_id,
                "from": {"id": int(from_id), "is_bot": False, "first_name": "T"},
                "chat": {"id": int(from_id), "type": "private"},
                "date": 1700000000,
                **content,
            },
        )

    def _voice(self, **kwargs):
        self._message(voice={"file_id": "voice-1", "duration": 5}, **kwargs)

    def _press(self, action, from_id=LINKED_TELEGRAM_ID):
        self.controller._dispatch_callback_query(
            self.bot.sudo(),
            {
                "id": "cb",
                "from": {"id": int(from_id), "is_bot": False, "first_name": "T"},
                "message": {
                    "message_id": 1,
                    "chat": {"id": int(from_id), "type": "private"},
                },
                "data": f"tk:{action}",
            },
        )

    def _chat(self, telegram_id=LINKED_TELEGRAM_ID):
        return self.bot.chat_ids.filtered(lambda chat: chat.chat_id == telegram_id)

    def _tasks(self):
        return self.env["project.task"].search([("create_uid", "=", self.user.id)])

    # Criterion 1: a voice note starts the flow and shows the transcript.

    def test_voice_note_starts_flow_and_shows_transcript(self):
        self._voice()
        chat = self._chat()
        self.assertEqual(chat.state, "awaiting_input")
        self.assertEqual(chat.context_data["narrative"], "dictado")
        self.assertIn("«dictado»", self._sent_texts()[-1])
        self.assertEqual(self.transcribe.call_args.kwargs["prompt"], "Odoo, Vykin")

    def test_failed_transcription_keeps_flow_open(self):
        self.transcribe.return_value = None
        self._voice()
        self.assertIn("No pude transcribir", self._sent_texts()[-1])
        self.assertEqual(self._chat().state, "awaiting_input")

    # Criterion 2: confirming creates the task, assigned, with photos and link.

    def test_confirm_creates_assigned_task_with_photos_and_link(self):
        self.bot.project_task_project_id = self.project
        self._voice()
        self._message(photo=[{"file_id": "small"}, {"file_id": "large"}])
        self._press("continue")
        self.assertIn("Revisar facturas", self._sent_texts()[-1])
        self._press("edit_title")
        self._message(text="Revisar facturas de octubre")
        self._press("edit_desc")
        self._message(text="Cuadrar contra el banco.")
        self._press("create")

        task = self._tasks()
        self.assertEqual(len(task), 1)
        self.assertEqual(task.name, "Revisar facturas de octubre")
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.user_ids, self.user)
        self.assertIn("Cuadrar contra el banco.", task.description)
        attachments = self.env["ir.attachment"].search(
            [("res_model", "=", "project.task"), ("res_id", "=", task.id)]
        )
        self.assertEqual(len(attachments), 1)
        self.assertIn(f"/odoo/project.task/{task.id}", self._sent_texts()[-1])
        self.assertEqual(self._chat().state, "idle")

    def test_picker_when_bot_has_no_default_project(self):
        self._message(text="Llamar al cliente")
        self._press("continue")
        self.assertIn("¿En qué proyecto?", self._sent_texts()[-1])
        self.chat_json.assert_not_called()
        self._press(f"project:{self.project.id}")
        self._press("create")
        self.assertEqual(self._tasks().project_id, self.project)

    def test_discard_leaves_no_task(self):
        self._message(text="Llamar al cliente")
        self._press("discard")
        self.assertEqual(self._chat().state, "idle")
        self._press("create")
        self.assertIn("ya no está activo", self._sent_texts()[-1])
        self.assertFalse(self._tasks())

    # Criterion 3: no permission on the project.

    def test_project_without_access_is_rejected(self):
        self._message(text="Llamar al cliente")
        self._press("continue")
        self.assertNotIn(
            "Dirección", str(self.api_post.call_args.kwargs["json"]["reply_markup"])
        )
        # A forged button for a project the user cannot read.
        self._press(f"project:{self.private_project.id}")
        self.assertIn("No tienes acceso a ese proyecto", self._sent_texts()[-1])
        self.assertNotIn("project_id", self._chat().context_data)
        self.assertFalse(self._tasks())

    def test_default_project_without_access_falls_back_to_picker(self):
        self.bot.project_task_project_id = self.private_project
        self._message(text="Llamar al cliente")
        self._press("continue")
        texts = self._sent_texts()
        self.assertIn("No tienes acceso al proyecto por defecto", texts[-2])
        self.assertIn("¿En qué proyecto?", texts[-1])

    def test_access_lost_before_creating_is_rejected(self):
        self.bot.project_task_project_id = self.project
        self._message(text="Llamar al cliente")
        self._press("continue")
        self.user.group_ids -= self.env.ref("project.group_project_user")
        self._press("create")
        self.assertTrue(self._sent_texts()[-1].startswith("🚫"))
        self.assertFalse(self._tasks())
        self.assertEqual(self._chat().state, "awaiting_input", "the draft stays")

    # Criterion 4: no AI, or an invalid answer, gives a draft from the text.

    def test_invalid_ai_answer_drafts_from_user_text(self):
        self.bot.project_task_project_id = self.project
        for answer in (None, {"title": 5, "description": "x"}, {"title": " "}):
            with self.subTest(answer=answer):
                self.chat_json.return_value = answer
                self._message(text="Llamar al cliente\nPor la factura vencida")
                self._press("continue")
                self._press("create")
                task = self._tasks()[:1]
                self.assertEqual(task.name, "Llamar al cliente")
                self.assertIn("factura vencida", task.description)
                task.unlink()

    def test_ai_title_is_trimmed(self):
        self.bot.project_task_project_id = self.project
        self.chat_json.return_value = {"title": "x" * 500, "description": "y"}
        self._message(text="Llamar al cliente")
        self._press("continue")
        self._press("create")
        self.assertEqual(len(self._tasks().name), 120)

    # Only a linked internal user gets the flow.

    def test_unlinked_or_portal_user_gets_no_flow(self):
        for telegram_id, answer in (
            (UNLINKED_TELEGRAM_ID, "Escribe /start"),
            (PORTAL_TELEGRAM_ID, "Solo puedo procesar comandos"),
        ):
            with self.subTest(telegram_id=telegram_id):
                self._voice(from_id=telegram_id)
                self.assertEqual(self._chat(telegram_id).state, "idle")
                self.assertIn(answer, self._sent_texts()[-1])
                self._press("create", from_id=telegram_id)
                self.assertIn("Escribe /start", self._sent_texts()[-1])
        self.transcribe.assert_not_called()
        self.assertFalse(self.env["project.task"].search([("name", "=", "dictado")]))

    def test_command_disabled_on_bot_gives_no_flow(self):
        self.bot.command_ids = False
        self._voice()
        self.assertEqual(self._chat().state, "idle")
        self.transcribe.assert_not_called()

    def test_nueva_command_starts_flow_with_seed(self):
        self._message(text="/nueva Llamar al cliente")
        chat = self._chat()
        self.assertEqual(chat.state, "awaiting_input")
        self.assertEqual(chat.context_data["narrative"], "Llamar al cliente")
