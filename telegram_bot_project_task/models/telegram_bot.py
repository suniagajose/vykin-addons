from odoo import fields, models


class TelegramBot(models.Model):
    _inherit = "telegram.bot"

    project_task_project_id = fields.Many2one(
        comodel_name="project.project",
        string="Default Project",
        help="Project where tasks created from Telegram are filed. The user can "
        "still change it before creating the task. Empty: the user picks one.",
    )
    project_task_glossary = fields.Text(
        string="Glossary",
        help="Terms, names and acronyms used at work, separated by commas. They "
        "help the AI transcribe voice notes and write the task.",
    )
