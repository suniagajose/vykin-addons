from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_distributed = fields.Boolean(
        string="Distributed",
        help="The collected amount of this product is provisioned according to the distribution "
             "scheme of the sales order.",
    )
