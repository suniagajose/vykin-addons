from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount


class AccountMove(models.Model):
    _inherit = 'account.move'

    # What a reconciliation provisioned: the provision entries and, for the salesperson, a draft vendor
    # bill. The link to the reconciliation is how Odoo links the cash basis entries to theirs.
    provision_partial_id = fields.Many2one(
        'account.partial.reconcile', string="Provisioned Reconciliation", readonly=True, copy=False,
        index='btree_not_null',
        help="The reconciliation that created this provision entry or commission bill.",
    )
    provision_origin_move_id = fields.Many2one(
        'account.move', string="Provisioned Document", readonly=True, copy=False, index='btree_not_null',
        help="The customer invoice or credit note this provision entry comes from.",
    )

    def _get_subcontracting_bill_label(self):
        """ Name of this bill in the messages about billed timesheets: a draft bill has no
        number yet, so its vendor tells it apart. """
        self.ensure_one()
        return f"{self.display_name} - {self.partner_id.display_name}"

    def _post(self, soft=True):
        # The values are those of the moment of posting (R2.1), and a line that provisions needs its
        # analytic account (R2.3). They are set before posting: a credit note with an origin is
        # reconciled with it while it is posted, and that reconciliation provisions.
        for move in self.filtered(lambda move: move.state == 'draft' and move.move_type in ('out_invoice', 'out_refund')):
            lines = move.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
            line_values = {line: line._get_provision_values() for line in lines}
            move._check_provision_lines(line_values)
            for line, values in line_values.items():
                line.write(values)
        # What the bills of this batch consume of a provision counts for the next ones.
        consumed = defaultdict(float)
        for bill in self.filtered(lambda move: move.state == 'draft' and move.move_type in ('in_invoice', 'in_receipt')):
            bill._check_provision_consumption(consumed)
        return super()._post(soft=soft)

    def _check_provision_consumption(self, consumed):
        """ A vendor bill or receipt cannot consume more of a provision than its balance in an analytic
        account, before the bill (and by commercial entity of the vendor, in commissions) (R6). A line
        distributed on several analytic accounts counts in each one for its percentage.
        ``consumed`` accumulates what the earlier bills of the same posting consume. """
        self.ensure_one()
        lines = self.invoice_line_ids.filtered(lambda line: line.display_type == 'product' and line.account_id.provision_type)
        if not lines:
            return
        if no_analytic := lines.filtered(lambda line: not line.analytic_distribution):
            raise UserError(self.env._(
                "These lines are against a provision, so they need an analytic account: %s",
                [line.name or line.account_id.display_name for line in no_analytic],
            ))
        no_partner = self.env['res.partner']
        amounts = defaultdict(float)
        for line in lines:
            partner = self.commercial_partner_id if line.account_id.provision_type == 'commission' else no_partner
            for analytic_id, percentage in line._get_provision_analytic_shares():
                amounts[line.account_id, analytic_id, partner] += line.balance * percentage / 100.0
        balances = self.env['account.move.line']._get_provision_balances(
            lines.account_id, {analytic_id for _account, analytic_id, _partner in amounts},
        )
        currency = self.company_currency_id
        problems = []
        for key, amount in amounts.items():
            total = amount + consumed[key]
            if currency.compare_amounts(total, balances[key]) > 0:
                account, analytic_id, partner = key
                problems.append(self.env._(
                    "%(account)s in %(analytic)s%(partner)s: %(amount)s against a balance of %(balance)s",
                    account=account.display_name,
                    analytic=self.env['account.analytic.account'].browse(analytic_id).display_name,
                    partner=f" ({partner.display_name})" if partner else "",
                    amount=format_amount(self.env, total, currency),
                    balance=format_amount(self.env, balances[key], currency),
                ))
        if problems:
            raise UserError(self.env._(
                "The provision does not cover this bill:\n%s", "\n".join(problems),
            ))
        for key, amount in amounts.items():
            consumed[key] += amount

    def _check_provision_lines(self, line_percents):
        """ A line with a percentage needs an analytic distribution (R2.3). A missing salesperson does
        not block: the reconciliation leaves its commission in income and says so (R3.6). """
        self.ensure_one()
        if no_analytic := [
            line for line, values in line_percents.items()
            if (values['provision_commission_percent'] or values['provision_implementation_percent'])
            and not line.analytic_distribution
        ]:
            raise UserError(self.env._(
                "These lines are provisioned for the commission or the implementation, so they need an "
                "analytic account: %s", [line.name or line.product_id.display_name for line in no_analytic],
            ))

    def _check_draftable(self):
        """ A customer invoice or credit note does not go back to draft while a reconciliation keeps a
        provision of it: its lines would no longer match their entries. Unreconciling its payments
        reverses that provision first (R7.5). """
        super()._check_draftable()
        if documents := self.filtered(lambda move: move.move_type in ('out_invoice', 'out_refund')):
            # sudo: the provision entries are system data; whoever resets the document must hear of them.
            if provisioned := self.sudo().search([
                ('provision_origin_move_id', 'in', documents.ids), ('provision_partial_id', '!=', False),
            ]).provision_origin_move_id:
                raise UserError(self.env._(
                    "These documents have a provision from their payments: unreconcile the payments first, "
                    "which reverses the provision, and then reset them to draft: %s",
                    provisioned.with_env(self.env).mapped('display_name'),
                ))
