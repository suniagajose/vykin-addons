from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    distribution_journal_id = fields.Many2one(
        related='company_id.distribution_journal_id', readonly=False,
    )
    distribution_commission_product_id = fields.Many2one(
        related='company_id.distribution_commission_product_id', readonly=False,
    )
