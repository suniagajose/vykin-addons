from odoo import api, fields, models
from odoo.exceptions import UserError


class SaleDistributionSettlement(models.TransientModel):
    _name = 'sale.distribution.settlement'
    _description = "Move Provision Balances to Expenses"

    project_id = fields.Many2one('project.project', required=True, readonly=True)
    summary = fields.Text(compute='_compute_summary')

    @api.depends('project_id')
    def _compute_summary(self):
        Line = self.env['account.move.line']
        for wizard in self:
            wizard.summary = "\n".join(
                f"{Line._format_distribution_key(key)}: {Line._format_distribution_amount(key, balance)}"
                for key, balance in wizard.project_id._get_distribution_balances().items()
            )

    def action_settle(self):
        """ Post the entry that moves each provision balance of the project to the expense
        account of its rule, then archive the project (R7.5, R7.5.1). """
        self.ensure_one()
        project = self.project_id
        balances = project._get_distribution_balances()
        if not balances:
            return project.action_archive()
        # sudo: the provision lines and the entry are system data, settled for whoever archives
        # the project (a project manager may have no accounting access).
        Line = self.env['account.move.line'].sudo()
        name = self.env._("Provision balance of %s moved to expenses", project.display_name)
        lines_vals = []
        order = None
        for (account, analytic_id, partner), balance in balances.items():
            source = Line.search([
                ('account_id', '=', account.id),
                ('distribution_rule_id', '!=', False),
                ('analytic_distribution', 'in', [analytic_id]),
            ], order='id desc', limit=1)
            if not source:
                raise UserError(self.env._(
                    "No provision entry explains the balance of %s: settle it with a manual entry.", account.display_name,
                ))
            order = source.distribution_order_id
            for vals in order._prepare_distribution_line_vals(source.distribution_rule_id, partner or None, -balance, name):
                vals['analytic_distribution'] = {str(analytic_id): 100.0}
                lines_vals.append(vals)
        move = self.env['account.move'].sudo().create(order._prepare_distribution_move_vals(
            lines_vals, fields.Date.context_today(self), name,
        ))
        move._post(soft=False)
        return project.action_archive()
