from odoo import Command
from odoo.exceptions import RedirectWarning, UserError
from odoo.tests import new_test_user, tagged

from .common import ProvisionCase


@tagged('post_install', '-at_install')
class TestProvisionCorrection(ProvisionCase):
    """ Acceptance cases CA-63 to CA-72 of the spec (R7 and R8), from state E1: F1 posted and half paid by
    P1, with the provision entry REP-1 and the draft commission bill FP-COM-1. """

    def _unreconcile(self, move):
        self._receivable(move).remove_move_reconcile()

    def _bill(self, partner, lines, post=False):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': partner.id, 'invoice_date': '2026-11-12',
            'invoice_line_ids': [Command.create({
                'name': "Line", 'account_id': account.id, 'quantity': 1, 'price_unit': price,
                'tax_ids': [Command.clear()], 'analytic_distribution': {self.analytic_x.id: 100},
            }) for account, price in lines],
        })
        if post:
            bill.action_post()
        return bill

    def _post_commission_bill(self, bill):
        bill.invoice_date = '2026-11-12'
        bill.action_post()

    def _pay_bill(self, bill):
        self.env['account.payment.register'].with_context(active_model='account.move', active_ids=bill.ids).create(
            {'payment_date': '2026-11-13'},
        )._create_payments()

    def test_63_unreconcile_reverses_and_cancels(self):
        self._e1()
        entry, bill = self._provision_entries(), self._commission_bills()
        self._unreconcile(self.f1)
        reversal = self._provision_entries() - entry
        self.assertFalse(reversal)  # the reversal is not tied to a document: it is the inverse of the entry
        reversal = self.env['account.move'].search([('reversed_entry_id', '=', entry.id)])
        self.assertEqual(len(reversal), 1)
        self.assertEqual((reversal.state, str(reversal.date)), ('posted', '2026-11-10'))
        ax, ay = (self.analytic_x.id,), (self.analytic_y.id,)
        self.assertEqual(self._items(reversal), sorted([
            ('205.02.02', 1000.0, 0.0, "Comisionistas SA", ax), ('601.74.01', 0.0, 1000.0, "Comisionistas SA", ax),
            ('205.02.03', 3750.0, 0.0, "", ax), ('501.02.02', 0.0, 3750.0, "", ax),
            ('205.02.02', 200.0, 0.0, "Comisionistas SA", ay), ('601.74.01', 0.0, 200.0, "Comisionistas SA", ay),
        ]))
        self.assertEqual(bill.state, 'cancel')
        for provision, analytic in ((self.commission_provision, self.analytic_x), (self.commission_provision, self.analytic_y),
                                    (self.implementation_provision, self.analytic_x)):
            self.assertEqual(self._balance(provision, analytic), 0.0)

    def test_64_reversal_in_a_locked_period(self):
        self._e1()
        self.company_a.fiscalyear_lock_date = '2026-11-30'
        self._unreconcile(self.f1)
        reversal = self.env['account.move'].search([('reversed_entry_id', '=', self._provision_entries().id)])
        self.assertEqual(str(reversal.date), '2026-12-01')

    def test_65_only_the_unreconciled_payment_is_reversed(self):
        self._e1()
        self._pay(self.f1, 7540.0, '2026-11-20')
        first, second = self._provision_entries()
        bill_1, bill_2 = self._commission_bills()
        self._unreconcile(self.f1.matched_payment_ids.sorted('id')[-1].move_id)
        self.assertTrue(self.env['account.move'].search([('reversed_entry_id', '=', second.id)]))
        self.assertFalse(self.env['account.move'].search([('reversed_entry_id', '=', first.id)]))
        self.assertEqual((bill_1.state, bill_2.state), ('draft', 'cancel'))

    def _paid_commission_bill(self):
        bill = self._commission_bills()
        self._post_commission_bill(bill)
        self._pay_bill(bill)
        return bill

    def test_66_commission_already_billed_blocks_unreconciling(self):
        self._e1()
        bill = self._paid_commission_bill()
        other = self._bill(self.beto.partner_id, [(self.commission_expense, 10.0)])
        cancelled = self._bill(self.ana.partner_id, [(self.commission_provision, 10.0)])
        cancelled.button_cancel()
        with self.assertRaises(RedirectWarning) as caught, self.cr.savepoint():
            self._unreconcile(self.f1)
        message, action, button = caught.exception.args[:3]
        self.assertIn(bill.name, message)
        self.assertIn("Ana Pérez, Posted, paid", message)
        for step in ("1. ", "2. ", "3. "):
            self.assertIn(step, message)
        self.assertEqual(action['res_model'], 'account.move')
        # The button opens only the bill that blocks: not the one of another entity nor the cancelled one.
        self.assertEqual([action['res_id']] if action.get('res_id') else action['domain'][0][2], [bill.id])
        self.assertTrue(self.f1.payment_state in ('partial', 'in_payment'))

    def test_67_unblocking(self):
        self._e1()
        bill = self._paid_commission_bill()
        # 1. break the reconciliation of its payment; 2. reset it to draft and cancel it.
        bill.line_ids.filtered(lambda line: line.account_id.account_type == 'liability_payable').remove_move_reconcile()
        bill.button_draft()
        bill.button_cancel()
        entry = self._provision_entries()
        self._unreconcile(self.f1)
        self.assertTrue(self.env['account.move'].search([('reversed_entry_id', '=', entry.id)]))
        self.assertEqual(self._balance(self.commission_provision, self.analytic_x), 0.0)

    def test_68_credit_note_returned_after_the_commission_was_billed(self):
        self._e1()
        self._post_commission_bill(self._commission_bills())
        nc2 = self._nc2()
        before = self._provision_entries()
        with self.assertRaises(RedirectWarning) as caught, self.cr.savepoint():
            self._pay(nc2, 1160.0, '2026-11-15')
        self.assertIn("Posted, not paid", caught.exception.args[0])
        self.assertEqual(self._provision_entries(), before)
        self.assertEqual(nc2.payment_state, 'not_paid')

    def test_69_negative_implementation_is_allowed_and_blocks_bills(self):
        self._e1()
        self._bill(self.carla, [(self.implementation_provision, 3750.0)], post=True)
        self._unreconcile(self.f1)
        self.assertEqual(self._balance(self.implementation_provision, self.analytic_x), -3750.0)
        with self.assertRaises(UserError):
            self._bill(self.carla, [(self.implementation_provision, 2000.0)], post=True)

    def test_70_entry_cannot_go_back_to_draft(self):
        self._e1()
        with self.assertRaises(UserError):
            self._provision_entries().button_draft()

    def test_71_entry_cannot_be_changed(self):
        self._e1()
        entry = self._provision_entries()
        item = entry.line_ids.filtered(lambda line: line.account_id == self.commission_provision and line.analytic_distribution == {str(self.analytic_x.id): 100.0})
        for vals, record in (
            ({'date': '2026-11-11'}, entry),
            ({'account_id': self.other_liability.id}, item),
            ({'credit': 999.0}, item),
            ({'partner_id': self.beto.partner_id.id}, item),
        ):
            with self.subTest(vals=vals), self.assertRaises(UserError), self.cr.savepoint():
                record.write(vals)

    def test_72_query_by_analytic_account_and_access(self):
        self._e1()
        self._post_commission_bill(self._commission_bills())
        self._bill(self.carla, [(self.implementation_provision, 3100.0)], post=True)
        lines = self.env['account.analytic.line'].search([('account_id', '=', self.analytic_x.id)])
        by_account = sorted(
            (line.general_account_id.code, line.partner_id.name or "", line.amount) for line in lines
            if line.general_account_id.code in ('601.74.01', '205.02.02', '501.02.02', '205.02.03')
        )
        self.assertEqual(by_account, sorted([
            ('601.74.01', "Comisionistas SA", -1000.0),
            ('205.02.02', "Comisionistas SA", 1000.0), ('205.02.02', "Comisionistas SA", -1000.0),
            ('501.02.02', "", -3750.0), ('205.02.03', "", 3750.0), ('205.02.03', "Carla Ruiz", -3100.0),
        ]))
        self.assertIn(10000.0, lines.filtered(lambda line: line.partner_id == self.partner_a).mapped('amount'))

        # Company B has its own provision entry and vendor bill; a user sees those of the companies active in the session.
        company_b = self.company_b
        entry_b = self.env['account.move'].with_company(company_b).create({
            'move_type': 'entry', 'journal_id': company_b.provision_journal_id.id, 'date': '2026-11-10',
            'line_ids': [
                Command.create({'account_id': company_b.provision_commission_expense_account_id.id, 'debit': 10.0}),
                Command.create({'account_id': company_b.provision_implementation_expense_account_id.id, 'credit': 10.0}),
            ],
        })
        entry_b.action_post()
        user = new_test_user(
            self.env, login='umulti', groups='account.group_account_invoice',
            company_id=self.company_a.id, company_ids=[Command.set((self.company_a | company_b).ids)],
        )
        journals = self.company_a.provision_journal_id | company_b.provision_journal_id
        Move = self.env['account.move'].with_user(user)
        only_a = Move.with_context(allowed_company_ids=[self.company_a.id]).search([('journal_id', 'in', journals.ids)])
        both = Move.with_context(allowed_company_ids=[self.company_a.id, company_b.id]).search([('journal_id', 'in', journals.ids)])
        self.assertEqual(only_a.company_id, self.company_a)
        self.assertIn(entry_b, both)
        self.assertNotIn(entry_b, only_a)
