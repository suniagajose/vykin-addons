from collections import defaultdict
from datetime import timedelta

from odoo import Command, api, models
from odoo.exceptions import UserError
from odoo.tools import format_amount


class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        for partial in partials.sorted('id'):
            partial._create_provision()
        return partials

    def _create_provision(self, only=None):
        """ Provision what this reconciliation brings in for each posted customer invoice or credit note it
        reconciles (only those of ``only``, if given), and create the commission bill of each salesperson
        (R3, R4). """
        self.ensure_one()
        # {salesperson: {(provision account, analytic account id): net}}
        commission_net = defaultdict(lambda: defaultdict(float))
        documents = self.env['account.move']
        for line, counterpart in ((self.debit_move_id, self.credit_move_id), (self.credit_move_id, self.debit_move_id)):
            document = line.move_id
            if (
                document.move_type not in ('out_invoice', 'out_refund') or document.state != 'posted'
                or line.account_id.account_type != 'asset_receivable' or (only is not None and document not in only)
            ):
                continue
            if self._create_provision_entry(document, line, counterpart.move_id, commission_net):
                documents |= document
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
            entries._reverse_moves(default_values_list=[{
                # The period of the entry, unless it is locked.
                'date': lock_dates[-1][0] + timedelta(days=1) if (lock_dates := entry._get_violated_lock_dates(entry.date, False)) else entry.date,
                'ref': entry.env._("Reversal of: %s", entry.name),
            } for entry in entries], cancel=True)
        return super().unlink()

    def _get_receivable_amounts(self, document):
        """ Return {reconciliation: amount} that each reconciliation of the receivable of ``document``
        covers, in its currency. """
        receivable = document.line_ids.filtered(lambda aml: aml.account_id.account_type == 'asset_receivable')
        return {
            partial: partial.debit_amount_currency if partial.debit_move_id in receivable else partial.credit_amount_currency
            for partial in receivable.matched_debit_ids | receivable.matched_credit_ids
        }

    def _get_provision_portion(self, document, line):
        """ Return the share of the document this reconciliation covers and whether it is the one that
        leaves the document fully reconciled, both in the currency of the document (R3.1, R3.3). """
        amount = self.debit_amount_currency if line == self.debit_move_id else self.credit_amount_currency
        currency = document.currency_id
        # An exchange difference reconciles no amount in the currency of the document.
        if currency.is_zero(amount) or currency.is_zero(document.amount_total):
            return 0.0, False
        reconciled = sum(amount for partial, amount in self._get_receivable_amounts(document).items() if partial.id <= self.id)
        return amount / document.amount_total, currency.compare_amounts(reconciled, document.amount_total) >= 0

    def _get_earlier_portions(self, document):
        """ The shares of the document that the earlier reconciliations covered. """
        currency = document.currency_id
        return [
            amount / document.amount_total
            for partial, amount in self._get_receivable_amounts(document).items()
            if partial.id < self.id and not currency.is_zero(amount)
        ]

    def _get_provision_date(self, journal):
        """ The most recent date of the two documents or, if it is locked, the first open one (R3.4). """
        lock_date = journal.company_id._get_user_fiscal_lock_date(journal)
        return max(self.max_date, lock_date + timedelta(days=1))

    def _create_provision_entry(self, document, line, counterpart_move, commission_net):
        """ Create and post the provision entry of ``document`` for this reconciliation, if it has lines
        with a share to provision, and add to ``commission_net`` what it provisions of commission by
        salesperson, provision account and analytic account. Without a salesperson, the commission is
        not provisioned: it stays in income and a note on the document says so (R3.6). Return the
        entry. """
        self.ensure_one()
        provisioned_lines = document.invoice_line_ids.filtered(
            lambda aml: aml.provision_commission_percent or aml.provision_implementation_percent,
        )
        if not provisioned_lines:
            return self.env['account.move']
        portion, is_last = self._get_provision_portion(document, line)
        if not portion:
            return self.env['account.move']
        # The company of the document, which the reconciliation may not share (a credit note of one company
        # paid with a payment of another): its percentages, journal and accounts are the ones that count.
        company = document.company_id
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
        earlier_portions = self._get_earlier_portions(document) if is_last else []
        name = f"{document.name} - {counterpart_move.display_name}"
        salesperson = document.invoice_user_id
        line_vals_list = []
        unprovisioned = {}  # {line: commission left in income, without a salesperson}
        for provisioned_line in provisioned_lines:
            for role in ('commission', 'implementation'):
                percent = provisioned_line[f'provision_{role}_percent']
                if not percent:
                    continue
                target = -provisioned_line.balance * percent / 100.0
                if is_last:
                    # What is still due after the earlier reconciliations, so that the cents of the rounding
                    # are settled (R3.3). What an earlier one left in income, for want of a salesperson,
                    # stays in income (R3.6).
                    amount = currency.round(target) - sum(currency.round(target * portion) for portion in earlier_portions)
                else:
                    amount = currency.round(target * portion)
                if currency.is_zero(amount):
                    continue
                if role == 'commission' and not salesperson:
                    unprovisioned[provisioned_line] = amount
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
                        commission_net[salesperson][provision, analytic_id] += amount * share / 100.0
        if unprovisioned:
            self._post_unprovisioned_commission_note(document, counterpart_move, unprovisioned)
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

    def _post_unprovisioned_commission_note(self, document, counterpart_move, unprovisioned):
        """ Say on the document that the commission of this reconciliation stays in income because the
        document has no salesperson (R3.6). """
        currency = document.company_id.currency_id
        amount = self.debit_amount_currency if self.debit_move_id.move_id == document else self.credit_amount_currency
        # sudo: the system writes the note, whoever reconciles.
        document.sudo().message_post(
            body=self.env._(
                "The reconciliation with %(counterpart)s (%(amount)s) provisions no commission: the lines "
                "%(lines)s carry commission, but the document has no salesperson. Their share "
                "(%(commission)s) stays in income.",
                counterpart=counterpart_move.display_name,
                amount=format_amount(self.env, amount, document.currency_id),
                lines=[line.name or line.product_id.display_name for line in unprovisioned],
                commission=format_amount(self.env, sum(unprovisioned.values()), currency),
            ),
            subtype_xmlid='mail.mt_note',
        )

    def _create_commission_bills(self, documents, commission_net):
        """ Create a draft vendor bill for each salesperson whose commission provision of this
        reconciliation is positive in some analytic account, with a line per provision account and
        analytic account, against that provision account (R4.1). """
        self.ensure_one()
        invoice = documents.filtered(lambda move: move.move_type == 'out_invoice')[:1] or documents[:1]
        company = invoice.company_id
        currency = company.currency_id
        product = self.env.ref('project_subcontracting.product_commission')
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
                    'account_id': account.id,
                    'tax_ids': [Command.clear()],
                    'analytic_distribution': {str(analytic_id): 100.0},
                })
                for (account, analytic_id), net in nets.items()
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
