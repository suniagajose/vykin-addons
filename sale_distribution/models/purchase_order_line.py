from odoo import fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    distribution_accrual_id = fields.Many2one(
        'sale.distribution.accrual',
        string="Provisioned Payment",
        readonly=True, copy=False, index='btree_not_null', ondelete='set null',
        help="Customer payment whose salesperson provision this commission line pays.",
    )
