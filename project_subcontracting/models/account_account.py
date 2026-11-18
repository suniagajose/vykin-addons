from odoo import fields, models


class AccountAccount(models.Model):
    _inherit = 'account.account'

    provision_type = fields.Selection(
        selection=[('commission', "Commission"), ('implementation', "Implementation")],
        string="Provision Type",
        help="What the balance of this account provisions: the commission of the salespeople or the "
             "implementation work. Products and product categories only offer the accounts of their type.",
    )
