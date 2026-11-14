from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import RedirectWarning, UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    distribution_origin_move_id = fields.Many2one(
        'account.move',
        string="Distribution Origin",
        readonly=True, copy=False, index='btree_not_null',
        help="Customer credit note whose excess this provision reversal comes from.",
    )
    distribution_accrual_ids = fields.One2many(
        'sale.distribution.accrual', 'invoice_id', string="Distribution Provisions", readonly=True,
    )
    distribution_accrual_count = fields.Integer(compute='_compute_distribution_accrual_count')

    def _compute_distribution_accrual_count(self):
        counts = dict(self.env['sale.distribution.accrual']._read_group(
            [('invoice_id', 'in', self.ids)], ['invoice_id'], ['__count'],
        ))
        for move in self:
            move.distribution_accrual_count = counts.get(move, 0)

    # -------------------------------------------------------------------------
    # CRUD / ACTIONS
    # -------------------------------------------------------------------------

    def write(self, vals):
        posted = self.filtered(lambda move: move.state == 'posted')
        if posted and (
            ('distribution_origin_move_id' in vals and not self.env.su)
            or ('date' in vals and posted._get_distribution_entries())
        ):
            raise UserError(self.env._(
                "A posted provision entry cannot be changed. Corrections are made with separate entries.",
            ))
        return super().write(vals)

    def _post(self, soft=True):
        self.filtered(lambda move: move.move_type in ('in_invoice', 'in_receipt'))._distribution_check_vendor_bills()
        posted = super()._post(soft=soft)
        documents = posted.filtered(lambda move: move.move_type in ('out_invoice', 'out_refund'))
        # sudo: the flag of a posted line is set here only (R2.1).
        lines = documents.invoice_line_ids.sudo()
        flags = {line: line._get_distribution_flag() for line in lines}
        lines.filtered(lambda aml: flags[aml] and not aml.is_distributed).is_distributed = True
        lines.filtered(lambda aml: aml.is_distributed and not flags[aml]).is_distributed = False
        documents.filtered(
            lambda move: move.move_type == 'out_refund' and any(move.invoice_line_ids.mapped('is_distributed')),
        )._distribution_refund()
        return posted

    def _check_draftable(self):
        super()._check_draftable()
        if self._get_distribution_entries():
            raise UserError(self.env._(
                "A posted provision entry cannot be reset to draft. Corrections are made with separate entries.",
            ))

    def button_draft(self):
        # A credit note reset to draft gives back the provision its excess had reversed; it is
        # reversed again when the credit note is posted.
        refunds = self.filtered(lambda move: move.move_type == 'out_refund' and move.state == 'posted')
        if refunds:
            reversals = self.search([
                ('distribution_origin_move_id', 'in', refunds.ids),
                ('state', '=', 'posted'),
                ('reversal_move_ids', '=', False),
                ('reversed_entry_id', '=', False),
            ])
            reversals._distribution_reverse()
            reversals._distribution_check_balances()
        return super().button_draft()

    def action_view_distribution_accruals(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('sale_distribution.sale_distribution_accrual_action')
        action['domain'] = [('invoice_id', '=', self.id)]
        return action

    # -------------------------------------------------------------------------
    # DISTRIBUTION
    # -------------------------------------------------------------------------

    def _get_distribution_entries(self):
        """ Return the provision entries (and their reversals) among these moves. """
        return self.filtered(
            lambda move: move.move_type == 'entry' and any(move.line_ids.mapped('distribution_rule_id')),
        )

    def _get_distribution_lines_by_order(self):
        """ Return the distributed lines of this customer document, grouped by the sales
        orders that have a distribution scheme. """
        self.ensure_one()
        lines_by_order = defaultdict(lambda: self.env['account.move.line'])
        for line in self.invoice_line_ids.filtered('is_distributed'):
            order = line.sale_line_ids.order_id[:1]
            if order.distribution_id:
                lines_by_order[order] |= line
        return lines_by_order

    def _distribution_check_vendor_bills(self):
        """ A vendor bill line against a provision account needs an analytic account, and,
        by analytic account, cannot exceed the provision balance; for the salesperson
        provision, the balance of the vendor's commercial entity (R6). """
        candidates = self.line_ids.filtered(
            lambda aml: aml.display_type == 'product' and aml.account_id.account_type == 'liability_current',
        )
        if not candidates:
            return
        accounts, partner_accounts = self.env['sale.distribution.rule']._get_payable_accounts()
        lines = candidates.filtered(lambda aml: aml.account_id in accounts)
        if not lines:
            return
        missing = lines.filtered(lambda aml: not aml.analytic_distribution)
        if missing:
            raise UserError(self.env._(
                "Add an analytic account to the lines of %s against a provision account: the provision is "
                "checked by project.",
                missing.move_id.mapped('display_name'),
            ))
        requested = defaultdict(float)
        for line in lines:
            partner = line.move_id.commercial_partner_id if line.account_id in partner_accounts else line.partner_id.browse()
            for analytic_id, percentage in line._get_distribution_analytic_shares():
                requested[line.account_id, analytic_id, partner] += line.balance * percentage / 100.0
        balances = self.env['account.move.line']._get_distribution_balances(
            lines.account_id, {analytic_id for _account, analytic_id, _partner in requested},
        )
        Line = self.env['account.move.line']
        exceeded = [
            self.env._(
                "%(provision)s: %(requested)s billed, %(available)s available",
                provision=Line._format_distribution_key(key),
                requested=Line._format_distribution_amount(key, amount),
                available=Line._format_distribution_amount(key, balances.get(key, 0.0)),
            )
            for key, amount in requested.items()
            if self.company_id[:1].currency_id.compare_amounts(amount, balances.get(key, 0.0)) > 0
        ]
        if exceeded:
            raise UserError(self.env._(
                "The vendor bill exceeds the provision available: %s", exceeded,
            ))

    def _distribution_redo_refunds(self, orders):
        """ Reverse the credit note reversals of these orders and compute them again with
        what is owed now; return the reversed ones (R7.2). """
        domain = [
            ('distribution_origin_move_id', '!=', False),
            ('state', '=', 'posted'),
            ('reversal_move_ids', '=', False),
            ('reversed_entry_id', '=', False),
        ]
        refunds = self.search(domain + [('line_ids.distribution_order_id', 'in', orders.ids)]).distribution_origin_move_id
        # All the reversals of those credit notes, also for other orders: a credit note is
        # computed again as a whole.
        reversals = self.search(domain + [('distribution_origin_move_id', 'in', refunds.ids)])
        reversals._distribution_reverse()
        reversals.distribution_origin_move_id._distribution_refund()
        return reversals

    def _distribution_refund(self):
        """ Reverse the provision of what these credit notes credit beyond what was still
        owed of the distributed lines, by role, with the order's analytic account (R7.2). """
        done = self.search([('distribution_origin_move_id', 'in', self.ids), ('reversal_move_ids', '=', False)])
        moves = self.env['account.move']
        for refund in self - done.distribution_origin_move_id:
            currency = refund.company_currency_id
            for order, lines in refund._get_distribution_lines_by_order().items():
                credited = sum(lines.mapped('balance'))
                excess = currency.round(credited - max(order._get_distribution_owed(exclude_move=refund), 0.0))
                if currency.compare_amounts(excess, 0.0) <= 0:
                    continue
                weights = order._get_distribution_provisioned_by_salesperson() or {
                    salesperson.user_id: salesperson.percentage for salesperson in order.distribution_salesperson_ids
                }
                name = self.env._("Provision reversed by %s", refund.name)
                lines_vals = [
                    vals
                    for rule, beneficiary, amount in order._get_distribution_shares(excess, weights)
                    for vals in order._prepare_distribution_line_vals(rule, beneficiary, -amount, name)
                ]
                if lines_vals:
                    move_vals = order._prepare_distribution_move_vals(lines_vals, refund.date, name)
                    moves |= self.env['account.move'].create({**move_vals, 'distribution_origin_move_id': refund.id})
        if moves:
            moves._post(soft=False)
            moves._distribution_check_balances()

    def _distribution_reverse(self):
        """ Reverse these provision entries, dated as the original while its period is open,
        else on the first open date, which posting sets (R7.1). """
        if not self:
            return self
        reversals = self._reverse_moves([
            {'date': move.date, 'ref': self.env._("Reversal of: %s", move.name)}
            for move in self
        ])
        reversals._post(soft=False)
        return reversals

    def _distribution_check_balances(self):
        """ After a correction of these provision entries: a negative salesperson provision
        blocks it (R7.3); a negative implementation provision is recorded and announced on
        the orders (R7.3.1). """
        accounts, partner_accounts = self.env['sale.distribution.rule']._get_payable_accounts()
        analytic_ids = {
            analytic_id for line in self.line_ids for analytic_id, _percentage in line._get_distribution_analytic_shares()
        }
        Line = self.env['account.move.line']
        balances = Line._get_distribution_balances(accounts, analytic_ids)
        negative = {
            key: balance for key, balance in balances.items()
            if (self.company_id[:1] or self.env.company).currency_id.compare_amounts(balance, 0.0) < 0
        }
        commissions = [key for key in negative if key[0] in partner_accounts]
        if commissions:
            bills = self.env['account.move.line'].search([
                ('move_id.move_type', 'in', ('in_invoice', 'in_receipt')),
                ('parent_state', '!=', 'cancel'),
                ('account_id', 'in', [account.id for account, _analytic, _partner in commissions]),
                ('partner_id', 'in', [partner.id for _account, _analytic, partner in commissions]),
                ('analytic_distribution', 'in', [analytic_id for _account, analytic_id, _partner in commissions]),
            ]).move_id
            raise bills._get_distribution_blocking_error(
                self.env._("This correction would leave a negative commission provision: its commissions are already billed."),
                self.env._("Retry the correction: unreconcile the customer payment or post the credit note again."),
            )
        for key, balance in negative.items():
            self.line_ids.distribution_order_id.message_post(body=self.env._(
                "The provision %(provision)s is negative: %(balance)s. The next payments replenish it, and "
                "vendor bills against it are rejected until it is positive.",
                provision=Line._format_distribution_key(key), balance=Line._format_distribution_amount(key, balance),
            ))

    def _get_distribution_blocking_error(self, reason, retry):
        """ Error listing these commission vendor bills (number, vendor, state, payment), the
        steps to unblock the operation, and a button that opens the bills (R7.1.1, R7.3). """
        states = dict(self._fields['state']._description_selection(self.env))
        descriptions = [
            self.env._(
                "%(bill)s (%(vendor)s): %(state)s, %(payment)s",
                bill=bill.display_name,
                vendor=bill.partner_id.display_name,
                state=states.get(bill.state, bill.state),
                payment=(
                    self.env._("paid") if bill.payment_state in ('paid', 'in_payment')
                    else self.env._("partially paid") if bill.payment_state == 'partial'
                    else self.env._("not paid")
                ),
            )
            for bill in self
        ]
        message = self.env._(
            "%(reason)s\nBills: %(bills)s.\nTo unblock it: (1) if a bill is paid, even partially, unreconcile its payment; "
            "(2) reset the bill to draft and cancel it; (3) %(retry)s",
            reason=reason, bills=descriptions, retry=retry,
        )
        action = self._get_records_action(name=self.env._("Commission Bills"))
        return RedirectWarning(message, action, self.env._("Open the bills"))
