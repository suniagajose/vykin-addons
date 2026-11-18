from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import UserError


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    provision_commission_percent = fields.Float(
        string="Commission %", readonly=True, copy=False,
        help="Share of the subtotal of this line that provisions the commission of the salesperson "
             "when the invoice is paid. Kept when the invoice is posted.",
    )
    provision_implementation_percent = fields.Float(
        string="Implementation %", readonly=True, copy=False,
        help="Share of the subtotal of this line that provisions the implementation work when the "
             "invoice is paid. Kept when the invoice is posted.",
    )
    provision_commission_account_id = fields.Many2one(
        'account.account', string="Commission Provision", readonly=True, copy=False, ondelete='restrict',
        help="Account the commission of this line is provisioned in. Kept when the invoice is posted.",
    )
    provision_implementation_account_id = fields.Many2one(
        'account.account', string="Implementation Provision", readonly=True, copy=False, ondelete='restrict',
        help="Account the implementation work of this line is provisioned in. Kept when the invoice is posted.",
    )
    # Tags the journal items of the provision entries. It is copied, so that the reversal of an entry
    # carries the same tag with the opposite amount.
    provision_origin_line_id = fields.Many2one(
        'account.move.line', string="Provisioned Line", readonly=True, index='btree_not_null', ondelete='set null',
        help="The invoice or credit note line this provision item comes from.",
    )

    def write(self, vals):
        # The hash of the journal covers the amounts and accounts of an item, not its analytic distribution,
        # which the balances of the provision (R6) read: it cannot move a provision to another project.
        if 'analytic_distribution' in vals and self.filtered(
            lambda line: line.provision_origin_line_id and line.parent_state == 'posted',
        ):
            raise UserError(self.env._("The analytic distribution of a posted provision entry cannot be changed."))
        return super().write(vals)

    def _get_provision_values(self):
        """ Return the values this line of a customer invoice or credit note keeps when its document is
        posted (R2.1, R2.2): those of the line it credits, if any, else the percentages of its company
        and the provision accounts of its product, for each role in which the product has one. """
        self.ensure_one()
        fields_by_role = {
            'commission': ('provision_commission_percent', 'provision_commission_account_id'),
            'implementation': ('provision_implementation_percent', 'provision_implementation_account_id'),
        }
        values = dict.fromkeys((fname for names in fields_by_role.values() for fname in names), False)
        if not self.product_id:
            return values
        move = self.move_id
        if move.move_type == 'out_refund':
            credited = move.reversed_entry_id.invoice_line_ids.filtered(
                lambda line: line.display_type == 'product' and (
                    (line.sale_line_ids & self.sale_line_ids) if self.sale_line_ids
                    else line.product_id == self.product_id
                ),
            )[:1]
            if credited:
                return credited._convert_to_write({fname: credited[fname] for fname in values})
        company = move.company_id
        product = self.product_id.product_tmpl_id.with_company(company)
        for role, (percent_field, account_field) in fields_by_role.items():
            if account := product._get_provision_account(role):
                values[percent_field] = company[f'provision_{role}_percent']
                values[account_field] = account.id
        return values

    def _get_provision_analytic_shares(self):
        """ Yield (analytic account id, percentage) of this line in the project plan: a line distributed on
        several analytic accounts counts in each one for its percentage. The accounts of other plans are
        another dimension of the same amount: they stay on the items for the reports, but they do not
        count in the control of the provisions, or the same amount would count once per plan. """
        self.ensure_one()
        project_plan, _other_plans = self.env['account.analytic.plan']._get_all_plans()
        for key, percentage in (self.analytic_distribution or {}).items():
            for analytic in self.env['account.analytic.account'].browse(int(analytic_id) for analytic_id in key.split(',')):
                if analytic.root_plan_id == project_plan:
                    yield analytic.id, percentage

    def _has_provision_analytic(self):
        """ Whether this line has an analytic account of the project plan, which controls the provisions. """
        self.ensure_one()
        return next(self._get_provision_analytic_shares(), None) is not None

    def _get_provision_balances(self, accounts, analytic_ids):
        """ Return {(account, analytic account id, partner): credit balance} of the posted items on these
        provision accounts and analytic accounts. The partner is the commercial entity on the
        commission provision accounts, and empty on the others (R6, R7.2). A line distributed on several
        analytic accounts counts in each one for its percentage. """
        balances = defaultdict(float)
        if not accounts or not analytic_ids:
            return balances
        no_partner = self.env['res.partner']
        # sudo: provision balances are checked whatever journal items the user can read.
        items = self.sudo().search_fetch([
            ('account_id', 'in', accounts.ids),
            ('parent_state', '=', 'posted'),
            ('analytic_distribution', 'in', list(analytic_ids)),
        ], ['account_id', 'partner_id', 'analytic_distribution', 'balance'])
        for item in items:
            account = item.account_id.with_env(self.env)
            partner = item.partner_id.commercial_partner_id.with_env(self.env) if account.provision_type == 'commission' else no_partner
            for analytic_id, percentage in item._get_provision_analytic_shares():
                if analytic_id in analytic_ids:
                    balances[account, analytic_id, partner] -= item.balance * percentage / 100.0
        return balances
