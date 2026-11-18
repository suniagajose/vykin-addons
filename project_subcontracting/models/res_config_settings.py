from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    provision_commission_percent = fields.Float(related='company_id.provision_commission_percent', readonly=False)
    provision_implementation_percent = fields.Float(
        related='company_id.provision_implementation_percent', readonly=False,
    )
    provision_commission_expense_account_id = fields.Many2one(
        related='company_id.provision_commission_expense_account_id', readonly=False,
        domain="[('company_ids', 'parent_of', company_id)]",
    )
    provision_implementation_expense_account_id = fields.Many2one(
        related='company_id.provision_implementation_expense_account_id', readonly=False,
        domain="[('company_ids', 'parent_of', company_id)]",
    )
    provision_journal_id = fields.Many2one(
        related='company_id.provision_journal_id', readonly=False,
        domain="[('type', '=', 'general'), ('company_id', 'parent_of', company_id)]",
    )
