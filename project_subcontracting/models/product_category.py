from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    property_provision_commission_categ_id = fields.Many2one(
        'account.account', string="Commission Provision", company_dependent=True, ondelete='restrict',
        domain="[('provision_type', '=', 'commission')]",
        help="Where the commission of the sales of these products is provisioned when the customer pays. "
             "Empty, the one of the parent category; with none, the commission is not provisioned.",
    )
    property_provision_implementation_categ_id = fields.Many2one(
        'account.account', string="Implementation Provision", company_dependent=True, ondelete='restrict',
        domain="[('provision_type', '=', 'implementation')]",
        help="Where the implementation work of the sales of these products is provisioned when the customer "
             "pays. Empty, the one of the parent category; with none, it is not provisioned.",
    )

    def write(self, vals):
        result = super().write(vals)
        if not {'parent_id', 'property_provision_commission_categ_id', 'property_provision_implementation_categ_id'}.isdisjoint(vals):
            # sudo: whoever sets the accounts of a category must hear about every product it affects.
            self.env['product.template'].sudo().search([('categ_id', 'child_of', self.ids)])._check_provision_project()
        return result
