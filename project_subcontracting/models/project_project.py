from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProjectProject(models.Model):
    _inherit = 'project.project'

    allow_subcontracting = fields.Boolean(
        string="Subcontracting", compute='_compute_allow_subcontracting', store=True, readonly=False,
        help="The tasks of this project are run by subcontracted implementers. Each task has a "
             "subcontracting hour cost; once done, the hours each assignee logged are paid to them "
             "with a vendor bill against the implementation provision.",
    )

    @api.depends('allow_timesheets')
    def _compute_allow_subcontracting(self):
        # An option of the timesheets: turned off with them, left off when they come back (R5.8.1).
        for project in self:
            project.allow_subcontracting = project.allow_subcontracting and project.allow_timesheets

    @api.constrains('allow_subcontracting', 'allow_timesheets')
    def _check_allow_subcontracting(self):
        if projects := self.filtered(lambda project: project.allow_subcontracting and not project.allow_timesheets):
            raise ValidationError(self.env._(
                "Subcontracting needs the timesheets of the project: %s", ", ".join(projects.mapped('display_name')),
            ))

    def write(self, vals):
        result = super().write(vals)
        if not {'allow_subcontracting', 'allow_timesheets', 'active'}.isdisjoint(vals):
            self._check_provision_products()
        return result

    @api.ondelete(at_uninstall=False)
    def _unlink_except_provision_products(self):
        self._check_provision_products(deleting=True)

    def _check_provision_products(self, deleting=False):
        """ A project template used by a product with a provision account keeps Subcontracting, and is
        neither archived nor deleted (R1.5.1). """
        if templates := self.filtered('is_template'):
            # sudo: whoever changes a template must hear about the products that depend on it.
            products = self.env['product.template'].sudo().search([('project_template_id', 'in', templates.ids)])
            if wrong := products.filtered(
                lambda product: (deleting or not product._has_provision_project()) and product._has_provision_account(),
            ):
                raise ValidationError(self.env._(
                    "These products have a provision account and need their project template to stay active "
                    "with Subcontracting: %s", wrong.mapped('display_name'),
                ))
