from odoo import models
from odoo.exceptions import UserError


class ProjectProject(models.Model):
    _inherit = 'project.project'

    def write(self, vals):
        if vals.get('active') is False:
            with_balance = self.filtered(lambda project: project.active and project._get_distribution_balances())
            if with_balance:
                raise UserError(self.env._(
                    "%s still has provision balances. Archive it from the project to move them to the "
                    "expense accounts first.",
                    with_balance.mapped('display_name'),
                ))
        return super().write(vals)

    def action_archive(self):
        with_balance = self.filtered(lambda project: project.active and project._get_distribution_balances())
        if not with_balance:
            return super().action_archive()
        if len(with_balance) > 1:
            raise UserError(self.env._(
                "%s still have provision balances: archive them one by one.", with_balance.mapped('display_name'),
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Provision Balances"),
            'res_model': 'sale.distribution.settlement',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_project_id': with_balance.id},
        }

    def _get_distribution_balances(self):
        """ Return {(provision account, analytic id, partner): balance} of this project's
        analytic account, without the zero balances (R7.5). """
        self.ensure_one()
        analytic = self.account_id
        if not analytic:
            return {}
        accounts, _partner_accounts = self.env['sale.distribution.rule']._get_payable_accounts()
        currency = (analytic.company_id or self.env.company).currency_id
        return {
            key: currency.round(balance)
            for key, balance in self.env['account.move.line']._get_distribution_balances(accounts, {analytic.id}).items()
            if not currency.is_zero(balance)
        }
