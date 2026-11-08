from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

LOCKED_FIELDS = (
    'date', 'account_id', 'balance', 'debit', 'credit', 'amount_currency', 'partner_id', 'analytic_distribution',
)
# Set by the distribution only: when a line is created or its invoice posted.
SYSTEM_FIELDS = ('distribution_order_id', 'distribution_rule_id', 'is_distributed')


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    is_distributed = fields.Boolean(
        string="Distributed Line",
        readonly=True, copy=False,
        help="Whether the product of this invoice line was a distributed product when the "
             "invoice was posted.",
    )
    # The fields below tag the journal items of the provision entries. They are copied, so
    # that the reversal of an entry carries the same tags with the opposite amount.
    distribution_order_id = fields.Many2one(
        'sale.order', string="Distribution Order", readonly=True, index='btree_not_null',
        ondelete='restrict',
    )
    distribution_rule_id = fields.Many2one(
        'sale.distribution.rule', string="Distribution Rule", readonly=True,
        index='btree_not_null', ondelete='restrict',
    )

    def write(self, vals):
        posted = self.filtered(lambda aml: aml.parent_state == 'posted')
        if posted and (
            (set(vals).intersection(SYSTEM_FIELDS) and not self.env.su)
            or (set(vals).intersection(LOCKED_FIELDS) and posted.filtered('distribution_rule_id'))
        ):
            raise UserError(self.env._(
                "A posted provision entry cannot be changed. Corrections are made with separate entries.",
            ))
        return super().write(vals)

    def _get_distribution_analytic_shares(self):
        """ Yield (analytic account id, percentage) of this line: a line distributed on several
        analytic accounts counts in each one for its percentage (R6). """
        self.ensure_one()
        for key, percentage in (self.analytic_distribution or {}).items():
            for analytic_id in key.split(','):
                yield int(analytic_id), percentage

    def _get_distribution_balances(self, accounts, analytic_ids):
        """ Return {(account, analytic id, partner): credit balance} of the posted items on
        these provision accounts and analytic accounts. The partner is the commercial entity on
        the salesperson provision accounts, and empty on the others. """
        balances = defaultdict(float)
        if not accounts or not analytic_ids:
            return balances
        _accounts, partner_accounts = self.env['sale.distribution.rule']._get_payable_accounts()
        no_partner = self.env['res.partner']
        # sudo: provision balances are checked whatever journal items the user can read.
        lines = self.sudo().search_fetch([
            ('account_id', 'in', accounts.ids),
            ('parent_state', '=', 'posted'),
            ('analytic_distribution', 'in', list(analytic_ids)),
        ], ['account_id', 'partner_id', 'analytic_distribution', 'balance'])
        for line in lines:
            account = line.account_id.with_env(self.env)
            partner = line.partner_id.commercial_partner_id.with_env(self.env) if account in partner_accounts else no_partner
            for analytic_id, percentage in line._get_distribution_analytic_shares():
                if analytic_id in analytic_ids:
                    balances[account, analytic_id, partner] -= line.balance * percentage / 100.0
        return balances

    def _format_distribution_key(self, key):
        """ Label of a provision balance key: account, analytic account and beneficiary. """
        account, analytic_id, partner = key
        analytic = self.env['account.analytic.account'].browse(analytic_id)
        return ", ".join(record.display_name for record in (account, analytic, partner) if record)

    def _format_distribution_amount(self, key, amount):
        return format_amount(self.env, amount, key[0].company_ids[:1].currency_id)

    def _get_distribution_flag(self):
        """ Whether this customer invoice or credit note line is distributed: a credit note
        line follows the line of the invoice it credits, frozen when that invoice was posted;
        any other line follows its product. """
        self.ensure_one()
        credited = self.move_id.reversed_entry_id.invoice_line_ids.filtered(
            lambda line: (line.sale_line_ids & self.sale_line_ids) if self.sale_line_ids
            else line.product_id == self.product_id,
        )
        if self.move_id.move_type == 'out_refund' and credited:
            return any(credited.mapped('is_distributed'))
        return self.product_id.is_distributed
