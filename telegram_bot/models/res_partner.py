import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Restricted to administrators: whoever can set it decides which Telegram
    # account acts as this contact (and as its user) through the bot.
    telegram_user_id = fields.Char(
        string="Telegram User ID",
        copy=False,
        groups="base.group_system",
        help="Numeric ID of this person's Telegram account. Their private chat "
        "with the bot is linked to this contact. The bot shows it to them on "
        "/start.",
    )

    _telegram_user_id_uniq = models.Constraint(
        "UNIQUE (telegram_user_id)",
        "This Telegram User ID is already linked to another contact!",
    )

    @api.constrains("telegram_user_id")
    def _check_telegram_user_id(self):
        """Validate that the Telegram User ID is a positive number.

        :raises ValidationError: if the ID is not made only of digits
        """
        for partner in self:
            if partner.telegram_user_id and not re.fullmatch(
                r"[0-9]+", partner.telegram_user_id
            ):
                raise ValidationError(
                    self.env._("The Telegram User ID must contain only digits.")
                )
