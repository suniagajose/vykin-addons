from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_subcontracting_bill_label(self):
        """ Name of this bill in the messages about billed timesheets: a draft bill has no
        number yet, so its vendor tells it apart. """
        self.ensure_one()
        return f"{self.display_name} - {self.partner_id.display_name}"
