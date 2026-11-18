from collections import defaultdict
from datetime import timedelta

from odoo import Command, api, models
from odoo.exceptions import RedirectWarning, UserError
from odoo.tools import format_amount


class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        # In order, so that each one sees what the earlier ones provisioned (R3.3).
        for partial in partials.sorted('id'):
            partial._create_provision()
        return partials

    def _create_provision(self):
        """ Provision what this reconciliation brings in for each customer invoice or credit note it
        reconciles, and create the commission bill of each salesperson (R3, R4). """
        self.ensure_one()
        commission_net = defaultdict(lambda: defaultdict(float))  # {salesperson: {analytic account id: net}}
        documents = self.env['account.move']
        entries = self.env['account.move']
        for line, counterpart in ((self.debit_move_id, self.credit_move_id), (self.credit_move_id, self.debit_move_id)):
            document = line.move_id
            if document.move_type not in ('out_invoice', 'out_refund') or line.account_id.account_type != 'asset_receivable':
                continue
            if entry := self._create_provision_entry(document, line, counterpart.move_id, commission_net):
                documents |= document
                entries |= entry
        if documents:
            self._check_commission_balances(entries)
        if commission_net:
            self._create_commission_bills(documents, commission_net)

    def unlink(self):
        """ Unreconciling reverses the provision entries of these reconciliations and cancels the commission
        bills they created in draft (R7.1), as Odoo reverses the cash basis entries. """
        if not self:
            return super().unlink()
        # sudo: the entries and bills are system data, handled for whoever unreconciles.
        moves = self.env['account.move'].sudo().search([('provision_partial_id', 'in', self.ids)])
        entries = moves.filtered(lambda move: move.move_type == 'entry' and move.state == 'posted')
        if bills := moves.filtered(lambda move: move.move_type == 'in_invoice' and move.state == 'draft'):
            bills.button_cancel()
        if entries:
            reversals = entries._reverse_moves(default_values_list=[{
                # The period of the entry, unless it is locked.
                'date': lock_dates[-1][0] + timedelta(days=1) if (lock_dates := entry._get_violated_lock_dates(entry.date, False)) else entry.date,
                'ref': entry.env._("Reversal of: %s", entry.name),
            } for entry in entries], cancel=True)
            self._check_commission_balances(entries | reversals)
        return super().unlink()

    def _check_commission_balances(self, entries):
        """ A reconciliation or an unreconciliation cannot leave the commission provision of a commercial
        entity negative in an analytic account: the commission was already billed. The message tells how to
        unblock it (R7.2). A negative implementation provision is allowed: R6 blocks its bills (R7.3). """
        # sudo: system data, read for whoever reconciles or unreconciles.
        items = entries.sudo().line_ids.filtered(lambda item: item.account_id.provision_type == 'commission')
        keys = {
            (item.account_id, analytic_id, item.partner_id.commercial_partner_id)
            for item in items for analytic_id, _percentage in item._get_provision_analytic_shares()
        }
        if not keys:
            return
        currency = (self.company_id[:1] or self.env.company).currency_id
        balances = self.env['account.move.line']._get_provision_balances(
            self.env['account.account'].union([account for account, _analytic, _partner in keys]),
            {analytic_id for _account, analytic_id, _partner in keys},
        )
        if negatives := [key for key in keys if currency.compare_amounts(balances[key], 0.0) < 0]:
            raise self._get_commission_block_error(negatives, balances)

    def _get_commission_block_error(self, negatives, balances):
        bills = self.env['account.move']
        details = []
        currency = self.env.company.currency_id
        for account, analytic_id, entity in negatives:
            analytic = self.env['account.analytic.account'].browse(analytic_id)
            # sudo: whoever is blocked must see the bills that block them.
            entity_bills = self.env['account.move.line'].sudo().search([
                ('account_id', '=', account.id), ('move_id.move_type', 'in', ('in_invoice', 'in_receipt')),
                ('parent_state', '!=', 'cancel'), ('analytic_distribution', 'in', [analytic_id]),
            ]).move_id.filtered(lambda bill: bill.commercial_partner_id == entity)
            bills |= entity_bills
            details.append(self.env._(
                "%(entity)s in %(analytic)s would be left with %(balance)s. Its commission bills: %(bills)s",
                entity=entity.display_name, analytic=analytic.display_name,
                balance=format_amount(self.env, balances[account, analytic_id, entity], currency),
                bills=", ".join(
                    self.env._(
                        "%(bill)s (%(vendor)s, %(state)s, %(paid)s)",
                        bill=bill.display_name, vendor=bill.partner_id.display_name,
                        state=dict(bill._fields['state']._description_selection(self.env))[bill.state],
                        paid=self.env._("paid") if bill.payment_state in ('paid', 'in_payment') else self.env._("not paid"),
                    ) for bill in entity_bills
                ) or self.env._("none"),
            ))
        message = self.env._(
            "The commission was already billed, so this cannot be done:\n%(details)s\n\n"
            "To unblock it, in this order:\n"
            "1. If a bill is paid, break the reconciliation of its payment.\n"
            "2. Reset the bill to draft and cancel it.\n"
            "3. Repeat this operation.",
            details="\n".join(details),
        )
        # sudo: the action only lists the bills the message names.
        return RedirectWarning(
            message, bills.sudo()._get_records_action(name=self.env._("Commission Bills")), self.env._("Open the bills"),
        )

    def _get_provision_portion(self, document, line):
        """ Return the share of the document this reconciliation covers and whether it is the one that
        leaves the document fully reconciled, both in the currency of the document (R3.1, R3.3). """
        amount = self.debit_amount_currency if line == self.debit_move_id else self.credit_amount_currency
        currency = document.currency_id
        # An exchange difference reconciles no amount in the currency of the document.
        if currency.is_zero(amount) or currency.is_zero(document.amount_total):
            return 0.0, False
        receivable = document.line_ids.filtered(lambda aml: aml.account_id.account_type == 'asset_receivable')
        reconciled = sum(
            partial.debit_amount_currency if partial.debit_move_id in receivable else partial.credit_amount_currency
            for partial in (receivable.matched_debit_ids | receivable.matched_credit_ids).filtered(lambda p: p.id <= self.id)
        )
        return amount / document.amount_total, currency.compare_amounts(reconciled, document.amount_total) >= 0

    def _get_provision_date(self, journal):
        """ The most recent date of the two documents or, if it is locked, the first open one (R3.4). """
        lock_date = self.company_id._get_user_fiscal_lock_date(journal)
        return max(self.max_date, lock_date + timedelta(days=1))

    def _create_provision_entry(self, document, line, counterpart_move, commission_net):
        """ Create and post the provision entry of ``document`` for this reconciliation, if it has lines
        with a share to provision, and add to ``commission_net`` what it provisions of commission by
        salesperson and analytic account. Return the entry. """
        self.ensure_one()
        provisioned_lines = document.invoice_line_ids.filtered(
            lambda aml: aml.provision_commission_percent or aml.provision_implementation_percent,
        )
        if not provisioned_lines:
            return self.env['account.move']
        portion, is_last = self._get_provision_portion(document, line)
        if not portion:
            return self.env['account.move']
        company = self.company_id
        currency = company.currency_id
        journal = company.provision_journal_id
        if not (journal and company.provision_commission_expense_account_id and company.provision_implementation_expense_account_id):
            raise UserError(self.env._(
                "Set the provision journal and the expense accounts of %s in the Accounting settings.",
                company.display_name,
            ))
        # sudo: a reconciliation provisions on behalf of the system, whoever registers the payment
        # (an accountant may have no access to sales or purchases).
        partner = document.invoice_user_id.sudo().partner_id.commercial_partner_id
        provisioned = self._get_provisioned_so_far(provisioned_lines) if is_last else {}
        name = f"{document.name} - {counterpart_move.display_name}"
        line_vals_list = []
        for provisioned_line in provisioned_lines:
            for role in ('commission', 'implementation'):
                percent = provisioned_line[f'provision_{role}_percent']
                if not percent:
                    continue
                target = -provisioned_line.balance * percent / 100.0
                if is_last:
                    # What is still missing, so that the line is provisioned to the cent (R3.3).
                    amount = currency.round(target) - provisioned.get((provisioned_line.id, role), 0.0)
                else:
                    amount = currency.round(target * portion)
                if currency.is_zero(amount):
                    continue
                expense = company[f'provision_{role}_expense_account_id']
                provision = provisioned_line[f'provision_{role}_account_id']
                role_partner = partner if role == 'commission' else self.env['res.partner']
                common_vals = {
                    'name': name,
                    'partner_id': role_partner.id,
                    'analytic_distribution': provisioned_line.analytic_distribution,
                    'provision_origin_line_id': provisioned_line.id,
                }
                line_vals_list += [
                    {**common_vals, 'account_id': expense.id, 'debit': max(amount, 0.0), 'credit': max(-amount, 0.0)},
                    {**common_vals, 'account_id': provision.id, 'debit': max(-amount, 0.0), 'credit': max(amount, 0.0)},
                ]
                if role == 'commission':
                    for analytic_id, share in provisioned_line._get_provision_analytic_shares():
                        commission_net[document.invoice_user_id][analytic_id] += amount * share / 100.0
        if not line_vals_list:
            return self.env['account.move']
        entry = self.env['account.move'].sudo().with_company(company).create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'company_id': company.id,
            'date': self._get_provision_date(journal),
            'ref': name,
            'provision_partial_id': self.id,
            'provision_origin_move_id': document.id,
            'line_ids': [Command.create(vals) for vals in line_vals_list],
        })
        entry._post(soft=False)
        return entry

    def _get_provisioned_so_far(self, lines):
        """ Return {(line id, role): amount} that the posted provision entries already provisioned of these
        lines, reversals included (R3.3). """
        provisioned = defaultdict(float)
        # sudo: system data, read for whoever reconciles the document.
        items = self.env['account.move.line'].sudo().search_fetch([
            ('provision_origin_line_id', 'in', lines.ids),
            ('account_id.provision_type', '!=', False),
            ('parent_state', '=', 'posted'),
        ], ['provision_origin_line_id', 'account_id', 'balance'])
        for item in items:
            provisioned[item.provision_origin_line_id.id, item.account_id.provision_type] -= item.balance
        return provisioned

    def _create_commission_bills(self, documents, commission_net):
        """ Create a draft vendor bill for each salesperson whose commission provision of this
        reconciliation is positive in some analytic account, with a line per account (R4.1). """
        self.ensure_one()
        company = self.company_id
        currency = company.currency_id
        product = self.env.ref('project_subcontracting.product_commission')
        invoice = documents.filtered(lambda move: move.move_type == 'out_invoice')[:1] or documents[:1]
        counterpart = ((self.debit_move_id | self.credit_move_id).move_id - invoice)[:1]
        amount = self.debit_amount_currency if self.debit_move_id.move_id == invoice else self.credit_amount_currency
        description = self.env._(
            "Commission · %(counterpart)s · %(amount)s · %(invoice)s",
            counterpart=counterpart.display_name,
            amount=format_amount(self.env, amount, invoice.currency_id),
            invoice=invoice.name,
        )
        for user, nets in commission_net.items():
            lines = [
                Command.create({
                    'product_id': product.id,
                    'name': description,
                    'quantity': 1.0,
                    'price_unit': currency.round(net),
                    'tax_ids': [Command.clear()],
                    'analytic_distribution': {str(analytic_id): 100.0},
                })
                for analytic_id, net in nets.items()
                if currency.compare_amounts(net, 0.0) > 0
            ]
            if lines:
                # The payment that reconciles is registered without synchronizing the lines of invoices, and
                # that state outranks the context: the bill needs its payable line.
                # sudo: the system creates the bill, whoever registers the payment.
                bill_model = self.env['account.move'].sudo().with_company(company)
                with bill_model._disable_recursion(None, 'skip_invoice_sync', target=False):
                    bill_model.create({
                        'move_type': 'in_invoice',
                        'partner_id': user.sudo().partner_id.id,
                        'company_id': company.id,
                        'provision_partial_id': self.id,
                        'invoice_line_ids': lines,
                    })
