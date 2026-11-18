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
