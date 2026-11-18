from odoo import models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        self._check_provision_project()
        return super().action_confirm()

    def _check_provision_project(self):
        """ Products with a provision account go to the project of the order only when it has
        Subcontracting (R1.5.2). """
        for order in self:
            project = order.project_id
            if not project or project.allow_subcontracting:
                continue
            products = order.order_line.product_id.product_tmpl_id.with_company(order.company_id).filtered(
                lambda product: product._has_provision_account(),
            )
            if products:
                raise UserError(self.env._(
                    "%(order)s sends %(products)s to the project %(project)s, which does not have Subcontracting.",
                    order=order.display_name, products=products.mapped('display_name'), project=project.display_name,
                ))
