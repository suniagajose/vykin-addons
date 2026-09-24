from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSurveyUserInput(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey = cls.env["survey.survey"].create({"title": "Interest survey", "survey_type": "survey"})
        cls.question = cls.env["survey.question"].create(
            {
                "survey_id": cls.survey.id,
                "title": "Are you interested?",
                "question_type": "simple_choice",
                "suggested_answer_ids": [
                    (0, 0, {"value": "Yes", "generate_lead": True}),
                    (0, 0, {"value": "No"}),
                ],
            }
        )
        cls.answer_yes, cls.answer_no = cls.question.suggested_answer_ids
        cls.public_user = cls.env.ref("base.public_user")
        # An internal user who can complete surveys but has no access to CRM leads.
        cls.survey_user = new_test_user(cls.env, login="survey_only", groups="survey.group_survey_user")

    def _complete(self, answer, user=None):
        """Complete the survey as ``user``, or as the public user the way the controller does.

        The survey controller runs ``_mark_done`` on the participation in sudo mode.
        """
        user_input = self.survey._create_answer(user=user or self.public_user)
        user_input._save_lines(self.question, answer.id)
        if user:
            user_input = user_input.with_user(user)
        else:
            user_input = user_input.with_user(self.public_user).sudo()
        user_input._mark_done()
        return user_input

    def _get_note(self, user_input):
        messages = user_input.lead_id.sudo().message_ids
        return messages.filtered(lambda message: "Interest survey" in (message.body or ""))

    def test_01_note_posted_on_generated_lead(self):
        user_input = self._complete(self.answer_yes)
        self.assertTrue(user_input.lead_id, "survey_crm should have generated a lead")
        note = self._get_note(user_input)
        self.assertEqual(len(note), 1)
        self.assertIn("Are you interested?", note.body)
        self.assertIn("Yes", note.body)

    def test_02_no_lead_no_note(self):
        user_input = self._complete(self.answer_no)
        self.assertFalse(user_input.lead_id)
        notes = self.env["mail.message"].search([("body", "ilike", "Interest survey"), ("model", "=", "crm.lead")])
        self.assertFalse(notes)

    def test_03_note_is_internal_and_authored_by_odoobot(self):
        note = self._get_note(self._complete(self.answer_yes))
        self.assertEqual(note.author_id, self.env.ref("base.partner_root"))
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))

    def test_04_note_sends_no_email(self):
        note = self._get_note(self._complete(self.answer_yes))
        self.assertFalse(self.env["mail.mail"].search([("mail_message_id", "=", note.id)]))

    def test_05_note_posted_when_completed_without_crm_access(self):
        user_input = self._complete(self.answer_yes, user=self.survey_user)
        self.assertEqual(len(self._get_note(user_input)), 1)
