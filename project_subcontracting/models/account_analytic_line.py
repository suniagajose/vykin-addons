from odoo import api, fields, models
from odoo.exceptions import UserError


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    subcontracting_bill_line_id = fields.Many2one(
        'account.move.line', string="Subcontracting Bill Line",
        readonly=True, copy=False, index='btree_not_null', ondelete='set null',
    )
    subcontracting_bill_id = fields.Many2one(
        related='subcontracting_bill_line_id.move_id', string="Subcontracting Bill",
        groups='account.group_account_invoice',
    )
    subcontracting_billable = fields.Boolean(
        string="To Bill", compute='_compute_subcontracting_billable', search='_search_subcontracting_billable',
        help="Logged by an assignee on a done task of a project with Subcontracting, and not billed yet.",
    )

    @api.depends(
        'user_id', 'task_id.state', 'task_id.user_ids', 'task_id.allow_subcontracting',
        'subcontracting_bill_line_id.parent_state',
    )
    def _compute_subcontracting_billable(self):
        for timesheet in self:
            timesheet.subcontracting_billable = timesheet._is_subcontracting_billable()

    def _search_subcontracting_billable(self, operator, value):
        if operator != 'in':
            return NotImplemented
        # sudo: the bill state decides, whether or not the user can read bills; the
        # caller's search still applies its own access rules to the ids returned.
        candidates = self.sudo().search([
            ('task_id.state', '=', '1_done'),
            ('task_id.project_id.allow_subcontracting', '=', True),
            ('user_id', '!=', False),
            '|', ('subcontracting_bill_line_id', '=', False),
                 ('subcontracting_bill_line_id.parent_state', '=', 'cancel'),
        ])
        # "Logged by an assignee of its task" cannot be written as a domain.
        return [('id', 'in', candidates.filtered(lambda timesheet: timesheet.user_id in timesheet.task_id.user_ids).ids)]

    def _is_subcontracting_billable(self):
        self.ensure_one()
        task = self.task_id
        return bool(
            task.allow_subcontracting and task.state == '1_done'
            and self.user_id in task.user_ids and not self._is_subcontracting_billed()
        )

    def _is_subcontracting_billed(self):
        """ Whether this timesheet is on a vendor bill that is not cancelled, even in draft (R5.4). """
        self.ensure_one()
        # sudo: whoever can see the timesheet may know whether it is billed, without access to bills.
        return self.sudo().subcontracting_bill_line_id.parent_state not in (False, 'cancel')

    def _get_subcontracting_ineligibility(self):
        """ Return why this timesheet cannot be billed to its collaborator, or False if it can (R5.1, R5.6). """
        self.ensure_one()
        task = self.task_id
        if not task:
            return self.env._("it is not logged on a task")
        if not task.allow_subcontracting:
            return self.env._("its project does not have Subcontracting")
        if task.state != '1_done':
            return self.env._("its task is not done")
        if self.user_id not in task.user_ids:
            return self.env._("%s is not assigned to its task", self.user_id.name or self.employee_id.name)
        if self._is_subcontracting_billed():
            return self.env._("it is already billed on %s", self.sudo().subcontracting_bill_id._get_subcontracting_bill_label())
        if task.subcontracting_currency_id.is_zero(task.subcontracting_hour_cost):
            return self.env._("its task has no subcontracting hour cost")
        return False

    def _is_readonly(self):
        return super()._is_readonly() or self._is_subcontracting_billed()

    def _subcontracting_restricted_fields(self):
        """ Fields a billed timesheet keeps, because its bill pays them (R5.5). """
        return ['task_id', 'project_id', 'user_id', 'employee_id', 'date', 'unit_amount']

    def _check_can_write(self, vals):
        if not vals.keys().isdisjoint(self._subcontracting_restricted_fields()):
            self._check_subcontracting_not_billed()
        super()._check_can_write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_subcontracting_billed(self):
        self._check_subcontracting_not_billed()

    def _check_subcontracting_not_billed(self):
        linked = self.sudo().filtered('subcontracting_bill_line_id')
        if billed := linked.filtered(lambda timesheet: timesheet._is_subcontracting_billed()):
            raise UserError(self.env._(
                "These timesheets are billed to their collaborator and cannot be changed or deleted; cancel "
                "the bill to release them: %s",
                ", ".join(sorted(billed.subcontracting_bill_id.mapped(lambda bill: bill._get_subcontracting_bill_label()))),
            ))

    def action_bill_subcontracted_hours(self):
        """ Create one draft vendor bill per collaborator with a line per task for these
        timesheets, and link each timesheet to its line (R5.3, R5.6). """
        if not self:
            raise UserError(self.env._("Choose the timesheets to bill."))
        problems = [
            f"{timesheet.task_id.display_name or timesheet.display_name}, {timesheet.date}: {reason}"
            for timesheet in self
            if (reason := timesheet._get_subcontracting_ineligibility())
        ]
        if problems:
            raise UserError(self.env._("No vendor bill was created:\n%s", "\n".join(problems)))

        product = self.env.ref('project_subcontracting.product_subcontracted_hours')
        bills = self.env['account.move']
        # Bill lines follow the order of the tasks in their project.
        for (user, company), timesheets in self.sorted(lambda t: (t.task_id.sequence, t.task_id.id)).grouped(
            lambda timesheet: (timesheet.user_id, timesheet.task_id.company_id or self.env.company),
        ).items():
            timesheets_by_task = timesheets.grouped('task_id')
            bill = self.env['account.move'].with_company(company).create({
                'move_type': 'in_invoice',
                'partner_id': user.partner_id.id,
            })
            lines = self.env['account.move.line'].with_company(company).create([
                {**task._prepare_subcontracting_bill_line_vals(product, task_timesheets), 'move_id': bill.id}
                for task, task_timesheets in timesheets_by_task.items()
            ])
            for line, task_timesheets in zip(lines, timesheets_by_task.values()):
                # sudo: linking needs no right on the timesheets of others beyond what was checked above.
                task_timesheets.sudo().subcontracting_bill_line_id = line
            bills |= bill
        # Not the context of the timesheets: with is_timesheet, the analytic lines of the bill
        # would become timesheets when it is posted, valued at the employee's cost.
        return bills._get_records_action(name=self.env._("Vendor Bills"), context={'default_move_type': 'in_invoice'})
