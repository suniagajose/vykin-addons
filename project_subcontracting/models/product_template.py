from odoo import api, fields, models
from odoo.exceptions import ValidationError

PROVISION_CHECKED_FIELDS = {
    'categ_id', 'service_tracking', 'project_id', 'project_template_id',
    'property_provision_commission_id', 'property_provision_implementation_id',
}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    property_provision_commission_id = fields.Many2one(
        'account.account', string="Commission Provision", company_dependent=True, ondelete='restrict',
        domain="[('provision_type', '=', 'commission')]",
        help="Keep this field empty to use the default value from the product category.",
    )
    property_provision_implementation_id = fields.Many2one(
        'account.account', string="Implementation Provision", company_dependent=True, ondelete='restrict',
        domain="[('provision_type', '=', 'implementation')]",
        help="Keep this field empty to use the default value from the product category.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        products._check_provision_project()
        return products

    def write(self, vals):
        result = super().write(vals)
        if not PROVISION_CHECKED_FIELDS.isdisjoint(vals):
            self._check_provision_project()
        return result

    def _get_provision_account(self, role):
        """ Return the provision account of this product for a role, 'commission' or 'implementation': the
        own one, else the one of the closest category that has it (R1.2). """
        self.ensure_one()
        return (
            self[f'property_provision_{role}_id']
            or self._get_category_account(f'property_provision_{role}_categ_id')
        )

    def _get_product_accounts(self):
        accounts = super()._get_product_accounts()
        accounts['provision_commission'] = self._get_provision_account('commission')
        accounts['provision_implementation'] = self._get_provision_account('implementation')
        return accounts

    def _has_provision_account(self):
        self.ensure_one()
        return bool(self._get_provision_account('commission') or self._get_provision_account('implementation'))

    def _has_provision_project(self):
        """ Whether this product generates a project from a project template with Subcontracting. """
        self.ensure_one()
        template = self.project_template_id
        return bool(
            self.service_tracking in ('project_only', 'task_in_project')
            and template.is_template and template.active and template.allow_subcontracting
        )

    def _check_provision_project(self):
        """ A product with a provision account generates its project from a template with
        Subcontracting (R1.5). Not a constraint: the company dependent fields it reads are not set yet
        when the ORM checks the constraints of a creation. """
        if wrong := self.filtered(lambda product: product._has_provision_account() and not product._has_provision_project()):
            raise ValidationError(self.env._(
                "A product with a provision account must generate a project from a project template with "
                "Subcontracting. Fix: %s", wrong.mapped('display_name'),
            ))
