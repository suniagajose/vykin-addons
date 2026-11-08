from odoo import fields
from odoo.exceptions import RedirectWarning, UserError
from odoo.tests import tagged
from odoo.tools import format_amount, html2plaintext

from .common import DistributionCommon


@tagged('post_install', '-at_install')
class TestDistributionCorrection(DistributionCommon):
    """ R7, after PG1: implementation 375 in A1, commissions V1 60 and V2 40. """

    def setUp(self):
        super().setUp()
        self.order = self._create_order()
        self.invoice = self._invoice(self.order)
        self.payment = self._pay(self.invoice, 1000.0)
        self.v1 = self.user_v1.partner_id
        self.v2 = self.user_v2.partner_id

    def _implementation_bill(self, amount, paid=False):
        bill = self._bill(self.implementer_1, [(self.implementation_provision, amount, [(self.analytic_1, 100.0)])])
        if paid:
            self._pay_bill(bill)
        return bill

    def _commission(self, partner):
        return self._provision(self.commission_provision, self.analytic_1, partner)

    def _implementation(self):
        return self._provision(self.implementation_provision, self.analytic_1)

    def _amount(self, amount):
        return format_amount(self.env, amount, self.currency)

    def _last_message(self):
        return html2plaintext(self.order.message_ids[0].body)

    # R7.1
    def test_unreconcile_reverses_provision(self):
        # CA-60
        original = self._provision_moves(self.order)
        original_lines = original.line_ids.read(['account_id', 'balance'])
        self._unreconcile(self.payment)
        reversal = self._provision_moves(self.order) - original
        self.assertEqual(reversal.reversed_entry_id, original)
        self.assertEqual(reversal.state, 'posted')
        self.assertEqual(reversal.date, original.date)
        self.assertEqual(original.line_ids.read(['account_id', 'balance']), original_lines)
        self.assertEqual(self._implementation(), 0.0)
        self.assertEqual(self._commission(self.v1), 0.0)
        self.assertEqual(self._commission(self.v2), 0.0)
        self.assertEqual(self._commission_purchases(self.order).mapped('state'), ['cancel', 'cancel'])
        self.assertFalse(self._bases(self.order))

    def test_unreconcile_reversal_date(self):
        # R7.1: dated as the original while its period is open, else on the first open date.
        order = self._create_order()
        invoice = self._invoice(order, post=False)
        invoice.invoice_date = '2026-01-10'
        invoice.action_post()
        payment = self._pay(invoice, 1000.0, date='2026-01-15')
        original = self._provision_moves(order)
        self.assertEqual(str(original.date), '2026-01-15')
        self._unreconcile(payment)
        self.assertEqual(original.reversal_move_ids.date, original.date)
        payment_2 = self._pay(invoice, 1000.0, date='2026-01-20')
        original_2 = self._provision_moves(order).filtered(lambda move: str(move.date) == '2026-01-20')
        self.env.company.fiscalyear_lock_date = '2026-01-31'
        self._unreconcile(payment_2)
        self.assertGreater(str(original_2.reversal_move_ids.date), '2026-01-31')

    def test_unreconcile_after_credit_note_excess(self):
        # CA-60.1: the credit note excess is computed again with what is owed after unreconciling.
        self._create_refund(self.invoice, self.order.order_line[0], [600.0])
        self.assertEqual(self._implementation(), 300.0)
        self._unreconcile(self.payment)
        self.assertEqual(self._implementation(), 0.0)
        self.assertEqual(self._commission(self.v1), 0.0)
        self.assertEqual(self._commission(self.v2), 0.0)
        # The credit note is reconciled with the invoice: 400 x 1,000 / 1,400 (R2.2), then the rest.
        self._reconcile(self.payment, self.invoice)
        self.assertEqual(self._bases(self.order), [285.71])
        self._pay(self.invoice, 400.0)
        self.assertEqual(self._bases(self.order), [285.71, 114.29])
        self.assertAlmostEqual(self._implementation(), 300.0, delta=0.01)
        self.assertAlmostEqual(self._commission(self.v1), 48.0, delta=0.01)
        self.assertAlmostEqual(self._commission(self.v2), 32.0, delta=0.01)

    def test_unreconcile_after_credit_note_of_two_orders(self):
        # The credit note is computed again as a whole, also for the order not unreconciled.
        start = self._implementation()
        order_a = self._create_order(lines=[(self.product_implementation, 1000.0)])
        order_b = self._create_order(lines=[(self.product_implementation, 1000.0)], project=self.project_2)
        (order_a | order_b).action_confirm()
        invoice_1 = self._invoice(order_a, post=False)
        invoice_1.invoice_line_ids.quantity = 0.3
        invoice_1.action_post()
        payment_1 = self._pay(invoice_1, 300.0)
        invoice_2 = (order_a | order_b)._create_invoices()
        invoice_2.invoice_date = fields.Date.today()
        invoice_2.action_post()
        self._pay(invoice_2, 1700.0)
        self._create_refund(invoice_2, order_a.order_line | order_b.order_line, [700.0, 1000.0])
        self.assertEqual(self._implementation(), start + 225.0)
        self._unreconcile(payment_1)
        # A: still owed without the credit note 1,000 - 700 collected = 300, so the excess is
        # 400 and the provision base 700 - 400 = 300, as before.
        self.assertEqual(self._implementation(), start + 225.0)
        self.assertEqual(self._provision(self.implementation_provision, self.analytic_2), 0.0)

    # R7.1.1
    def test_unreconcile_blocked_by_draft_commission_bill(self):
        # CA-61
        bill = self._commission_bill(self._purchase_of(self.order, self.user_v2), post=False)
        with self.assertRaises(RedirectWarning) as error:
            self._unreconcile(self.payment)
        message = str(error.exception.args[0])
        self.assertIn(bill.display_name, message)
        self.assertIn("not paid", message)
        self.assertIn("(2)", message)
        self.assertEqual(error.exception.args[1]['res_id'], bill.id)
        self.assertEqual(len(self._provision_moves(self.order)), 1)

    def test_unreconcile_blocked_by_partially_paid_commission_bill(self):
        bill = self._commission_bill(self._purchase_of(self.order, self.user_v1))
        self._pay_bill(bill, 10.0)
        with self.assertRaises(RedirectWarning) as error:
            self._unreconcile(self.payment)
        self.assertIn("partially paid", str(error.exception.args[0]))

    def test_unreconcile_blocked_by_posted_commission_bills(self):
        # CA-61.1, CA-61.2
        bill_1 = self._commission_bill(self._purchase_of(self.order, self.user_v1))
        bill_payment = self._pay_bill(bill_1)
        bill_2 = self._commission_bill(self._purchase_of(self.order, self.user_v2))
        with self.assertRaises(RedirectWarning) as error:
            self._unreconcile(self.payment)
        message = str(error.exception.args[0])
        self.assertIn(bill_1.name, message)
        self.assertIn(bill_2.name, message)
        self.assertIn("(1)", message)
        self.assertEqual(set(error.exception.args[1]['domain'][0][2]), set((bill_1 | bill_2).ids))
        # Unblock as the message says.
        bill_payment.move_id.line_ids.filtered(lambda aml: aml.account_id.account_type == 'liability_payable').remove_move_reconcile()
        (bill_1 | bill_2).button_draft()
        (bill_1 | bill_2).button_cancel()
        self._unreconcile(self.payment)
        self.assertEqual(len(self._provision_moves(self.order)), 2)
        self.assertEqual(self._commission_purchases(self.order).mapped('state'), ['cancel', 'cancel'])

    # R7.2
    def test_credit_note_beyond_what_is_owed(self):
        # CA-62
        refund = self._create_refund(self.invoice, self.order.order_line[0], [600.0])
        reversal = self.env['account.move'].search([('distribution_origin_move_id', '=', refund.id)])
        self.assertEqual(reversal.state, 'posted')
        self.assertTrue(all(line.analytic_distribution == {str(self.analytic_1.id): 100.0} for line in reversal.line_ids))
        self.assertEqual(self._commission(self.v1), 48.0)
        self.assertEqual(self._commission(self.v2), 32.0)
        self.assertEqual(self._implementation(), 300.0)
        expense = reversal.line_ids.filtered(lambda aml: aml.account_id in (self.commission_expense | self.implementation_expense))
        self.assertEqual(sum(expense.mapped('credit')), 95.0)

    def test_credit_note_within_what_is_owed(self):
        # CA-63, CA-64
        refund = self._create_refund(self.invoice, self.order.order_line[0], [500.0])
        refund_2 = self._create_refund(self.invoice, self.order.order_line[1], [600.0])
        self.assertFalse(self.env['account.move'].search([('distribution_origin_move_id', 'in', (refund | refund_2).ids)]))
        self.assertEqual(self._implementation(), 375.0)

    def test_credit_note_reset_to_draft(self):
        # A credit note reset to draft gives back the provision; posted again, reverses it again.
        refund = self._create_refund(self.invoice, self.order.order_line[0], [600.0])
        refund.button_draft()
        self.assertEqual(self._implementation(), 375.0)
        refund.action_post()
        self.assertEqual(self._implementation(), 300.0)

    def _milestone_order(self):
        """ P3 in PR2 (A2): one Implementation line of 1,000 invoiced in two milestones of 500. """
        order = self._create_order(lines=[(self.product_implementation, 1000.0)], project=self.project_2)
        order.action_confirm()
        invoices = self.env['account.move']
        for _milestone in range(2):
            invoice = order._create_invoices()
            invoice.invoice_line_ids.quantity = 0.5
            invoice.invoice_date = fields.Date.today()
            invoice.action_post()
            invoices |= invoice
        return order, invoices

    def test_credit_note_on_one_milestone_not_reconciled(self):
        # CA-64.1: the credit note of the first milestone is not provisioned again with the second.
        order, (invoice_1, invoice_2) = self._milestone_order()
        self._pay(invoice_1, 500.0)
        refund = self._create_refund(invoice_1, order.order_line, [500.0])
        self.assertFalse(self.env['account.move'].search([('distribution_origin_move_id', '=', refund.id)]))
        self._pay(invoice_2, 500.0)
        self.assertEqual(self._bases(order), [500.0])

    def test_credit_note_on_one_milestone_after_both_paid(self):
        # CA-64.2
        order, (invoice_1, invoice_2) = self._milestone_order()
        self._pay(invoice_1, 500.0)
        self._pay(invoice_2, 500.0)
        refund = self._create_refund(invoice_1, order.order_line, [500.0])
        self.assertTrue(self.env['account.move'].search([('distribution_origin_move_id', '=', refund.id)]))
        self.assertEqual(self._provision(self.implementation_provision, self.analytic_2), 375.0)

    # R7.3
    def test_credit_note_leaves_commission_at_zero(self):
        # CA-66
        self._bill(self.v1, [(self.commission_provision, 48.0, [(self.analytic_1, 100.0)])])
        refund = self._create_refund(self.invoice, self.order.order_line[0], [600.0])
        self.assertEqual(refund.state, 'posted')
        self.assertEqual(self._commission(self.v1), 0.0)

    def test_credit_note_blocked_by_negative_commission(self):
        # CA-67
        bill = self._bill(self.v1, [(self.commission_provision, 48.01, [(self.analytic_1, 100.0)])])
        refund = self._create_refund(self.invoice, self.order.order_line[0], [600.0], post=False)
        with self.assertRaises(RedirectWarning) as error:
            refund.action_post()
        self.assertIn(bill.name, str(error.exception.args[0]))
        self.assertEqual(refund.state, 'draft')

    def test_credit_note_blocked_by_negative_commission_receipt(self):
        receipt = self._bill(self.v1, [(self.commission_provision, 48.01, [(self.analytic_1, 100.0)])], move_type='in_receipt')
        refund = self._create_refund(self.invoice, self.order.order_line[0], [600.0], post=False)
        with self.assertRaises(RedirectWarning) as error:
            refund.action_post()
        self.assertIn(receipt.name, str(error.exception.args[0]))

    def test_credit_note_with_negative_implementation(self):
        # CA-67.1, R7.3.1
        self._implementation_bill(375.0)
        refund = self._create_refund(self.invoice, self.order.order_line[0], [600.0])
        self.assertEqual(refund.state, 'posted')
        self.assertEqual(self._implementation(), -75.0)
        self.assertEqual(self._commission(self.v1), 48.0)
        self.assertEqual(self._commission(self.v2), 32.0)
        self.assertIn(self._amount(-75.0), self._last_message())

    # R7.3.1, R7.3.2
    def test_unreconcile_with_billed_implementation(self):
        # CA-65, CA-65.1
        self._implementation_bill(300.0, paid=True)
        self._unreconcile(self.payment)
        self.assertEqual(self._implementation(), -300.0)
        self.assertIn(self._amount(-300.0), self._last_message())
        with self.assertRaises(UserError):
            self._implementation_bill(10.0)
        self._reconcile(self.payment, self.invoice)
        self.assertEqual(self._implementation(), 75.0)
        with self.assertRaises(UserError):
            self._implementation_bill(75.01)
        self.assertEqual(self._implementation_bill(75.0).state, 'posted')

    # R7.4
    def test_provision_entry_is_locked(self):
        # CA-68, CA-69
        move = self._provision_moves(self.order)
        with self.assertRaises(UserError):
            move.button_draft()
        line = move.line_ids[0]
        with self.assertRaises(UserError):
            line.analytic_distribution = {str(self.analytic_2.id): 100.0}
        with self.assertRaises(UserError):
            line.partner_id = self.partner_a
        with self.assertRaises(UserError):
            move.date = '2020-01-01'
        # Fields set by the distribution only, also through RPC by an accountant.
        accountant = self._create_user('accountant', "Accountant", 'account.group_account_manager')
        invoice = self.invoice.with_user(accountant)
        with self.assertRaises(UserError):
            invoice.invoice_line_ids[0].distribution_order_id = self.order
        with self.assertRaises(UserError):
            invoice.invoice_line_ids[0].is_distributed = False
        with self.assertRaises(UserError):
            invoice.distribution_origin_move_id = self.invoice
        self._unreconcile(self.payment)
        reversal = self._provision_moves(self.order) - move
        with self.assertRaises(UserError):
            reversal.button_draft()

    # R7.5
    def test_archive_project_with_provision(self):
        # CA-70, CA-71
        action = self.project_1.action_archive()
        self.assertTrue(self.project_1.active)
        self.assertEqual(action['res_model'], 'sale.distribution.settlement')
        wizard = self.env['sale.distribution.settlement'].with_context(action['context']).create({})
        self.assertIn(self._amount(375.0), wizard.summary)
        self.assertIn(self._amount(60.0), wizard.summary)
        self.assertIn(self._amount(40.0), wizard.summary)
        with self.assertRaises(UserError):
            self.project_1.active = False
        with self.assertRaises(UserError):
            self.project_1.with_context(sale_distribution_checked=True).active = False
        wizard.action_settle()
        self.assertFalse(self.project_1.active)
        settlement = self.env['account.move.line'].search([
            ('distribution_rule_id', '!=', False), ('move_id.ref', 'like', 'moved to expenses'),
        ])
        self.assertEqual(sum(settlement.filtered(lambda aml: aml.account_id == self.implementation_expense).mapped('credit')), 375.0)
        self.assertEqual(sum(settlement.filtered(lambda aml: aml.account_id == self.commission_expense).mapped('credit')), 100.0)
        self.assertEqual(self._implementation(), 0.0)

    def test_archive_project_with_one_cent(self):
        # CA-70.1
        self._implementation_bill(374.99)
        self.assertTrue(self.project_1.action_archive())
        self.assertTrue(self.project_1.active)

    def test_archive_project_with_negative_provision(self):
        # CA-70.2
        self._implementation_bill(300.0, paid=True)
        self._unreconcile(self.payment)
        action = self.project_1.action_archive()
        wizard = self.env['sale.distribution.settlement'].with_context(action['context']).create({})
        self.assertIn(self._amount(-300.0), wizard.summary)
        wizard.action_settle()
        self.assertFalse(self.project_1.active)
        settlement = self.env['account.move.line'].search([('move_id.ref', 'like', 'moved to expenses')])
        self.assertEqual(settlement.filtered(lambda aml: aml.account_id == self.implementation_expense).debit, 300.0)

    def test_archive_project_without_provision(self):
        # CA-71.1
        self._implementation_bill(375.0)
        self._bill(self.v1, [(self.commission_provision, 60.0, [(self.analytic_1, 100.0)])])
        self._bill(self.v2, [(self.commission_provision, 40.0, [(self.analytic_1, 100.0)])])
        self.project_1.action_archive()
        self.assertFalse(self.project_1.active)
