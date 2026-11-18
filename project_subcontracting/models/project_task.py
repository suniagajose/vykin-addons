from collections import defaultdict

from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import float_compare, float_is_zero, formatLang


class ProjectTask(models.Model):
    _inherit = 'project.task'

    allow_subcontracting = fields.Boolean(related='project_id.allow_subcontracting')
    subcontracting_currency_id = fields.Many2one(related='project_id.currency_id', export_string_translation=False)
    subcontracting_hour_cost = fields.Monetary(
        string="Subcontracting Hour Cost",
        currency_field='subcontracting_currency_id',
        help="Price of each hour of this task on the vendor bills that pay its subcontracted assignees. "
             "It is not the hourly cost of the employee.",
    )
    subcontracting_warning = fields.Html(
        compute='_compute_subcontracting_warning', compute_sudo=True, sanitize=False,
        export_string_translation=False,
    )

    @api.depends(
        'allow_subcontracting', 'subcontracting_hour_cost', 'state', 'user_ids', 'allocated_hours', 'effective_hours',
        'timesheet_ids.user_id', 'timesheet_ids.unit_amount',
        'timesheet_ids.subcontracting_bill_line_id.parent_state',
    )
    def _compute_subcontracting_warning(self):
        for task in self:
            warnings = task._get_subcontracting_warnings() if task.allow_subcontracting else []
            task.subcontracting_warning = warnings and Markup('<ul class="mb-0">%s</ul>') % Markup().join(
                Markup('<li>%s</li>') % warning for warning in warnings
            ) or False

    def _get_subcontracting_warnings(self):
        """ Return a message for each incident of this task that departs from the subcontracting
        flow, saying what happens and what to do (R5.1.1, R5.7.1 to R5.7.3). """
        self.ensure_one()
        warnings = []
        if self.subcontracting_currency_id.is_zero(self.subcontracting_hour_cost):
            warnings.append(self.env._(
                "The subcontracting hour cost is 0: set it before billing the hours of this task.",
            ))
        hours = defaultdict(float)
        to_bill = defaultdict(float)
        for timesheet in self.timesheet_ids.filtered('user_id'):
            hours[timesheet.user_id] += timesheet.unit_amount
            if not timesheet._is_subcontracting_billed():
                to_bill[timesheet.user_id] += timesheet.unit_amount
        for user, user_hours in hours.items():
            if user not in self.user_ids:
                warnings.append(self.env._(
                    "%(user)s logged %(hours)s hours without being assigned: they cannot be billed. "
                    "Assign them to the task to bill their hours.",
                    user=user.name, hours=formatLang(self.env, user_hours),
                ))
            elif self.state == '1_done' and not float_is_zero(to_bill[user], precision_digits=2):
                warnings.append(self.env._(
                    "%(user)s has %(hours)s hours to bill: use Bill Hours on their timesheets.",
                    user=user.name, hours=formatLang(self.env, to_bill[user]),
                ))
        # The task's own hours, without its subtasks: the cost per hour is the task's.
        if self.allocated_hours and float_compare(self.effective_hours, self.allocated_hours, precision_digits=2) > 0:
            warnings.append(self.env._(
                "%(logged)s hours were logged and %(planned)s were planned: the extra hours are billed at the task's "
                "cost and take provision meant for other tasks. Adjust the planned hours or decide whether to pay them.",
                logged=formatLang(self.env, self.effective_hours), planned=formatLang(self.env, self.allocated_hours),
            ))
        return warnings

    def _prepare_subcontracting_bill_line_vals(self, product, timesheets):
        """ Return the values of the vendor bill line that pays these timesheets of this task. """
        self.ensure_one()
        return {
            'product_id': product.id,
            'name': self.env._("%(product)s - %(task)s", product=product.name, task=self.display_name),
            'quantity': sum(timesheets.mapped('unit_amount')),
            'price_unit': self.subcontracting_hour_cost,
            'analytic_distribution': self.project_id._get_analytic_distribution(),
        }
