from odoo import fields, models


class TelegramCommand(models.Model):
    """Registry of Telegram bot commands assignable to bots."""

    _name = "telegram.command"
    _description = "Telegram Command"

    name = fields.Char(
        string="Command",
        required=True,
        help="Command name including the '/' prefix (e.g., /start)",
    )
