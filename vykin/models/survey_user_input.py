from markupsafe import Markup

from odoo import SUPERUSER_ID, _, models


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    def _mark_done(self):
        """Log the survey answers in the chatter of the lead generated from the participation.

        ``survey_crm`` creates the lead (and sets ``lead_id``) inside its own ``_mark_done``, so
        the lead only exists once ``super()`` has run.
        """
        res = super()._mark_done()
        for user_input in self.filtered("lead_id"):
            # Public users cannot write on leads: the lead is created with sudo by survey_crm.
            # Posting as the superuser also makes OdooBot the author.
            lead = user_input.lead_id.with_user(SUPERUSER_ID)
            answers = user_input._prepare_lead_values_from_user_input_lines()["description"]
            body = Markup("<p>%(title)s</p>%(answers)s") % {
                "title": _('Survey "%s" completed.', user_input.survey_id.title),
                "answers": answers,
            }
            lead.message_post(body=body, subtype_xmlid="mail.mt_note")
        return res
