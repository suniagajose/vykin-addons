from odoo import fields
from odoo.tests import tagged

from .common import DistributionCommon


@tagged('post_install', '-at_install')
class TestDistributionPayment(DistributionCommon):

    # R2.1
    def test_flag_frozen_when_posted(self):
        # CA-16, CA-18: the product changes after F1 is posted; the base does not.
        order = self._create_order()
        invoice = self._invoice(order)
        self.product_license.is_distributed = True
        self.product_implementation.is_distributed = False
        self._pay(invoice, 1000.0)
        self.assertEqual(self._bases(order), [500.0])

    def test_flag_taken_when_posted(self):
        # CA-17
        order = self._create_order()
        invoice = self._invoice(order, post=False)
        self.product_license.is_distributed = True
        invoice.action_post()
        self._pay(invoice, 1000.0)
        self.assertEqual(self._bases(order), [1000.0])

    # R2.2
    def test_base_of_payments(self):
        # CA-19, CA-20
        order = self._create_order()
        invoice = self._invoice(order)
        self._pay(invoice, 1000.0)
        self._pay(invoice, 1000.0)
        self.assertEqual(self._bases(order), [500.0, 500.0])

    def test_provisioned_amounts(self):
        order = self._create_order()
        invoice = self._invoice(order)
        self._pay(invoice, 1000.0)
        accrual = order.distribution_accrual_ids
        self.assertRecordValues(accrual, [{
            'commission_amount': 100.0, 'implementation_amount': 375.0, 'provisioned_amount': 475.0,
        }])
        self.assertEqual(order.distribution_commission_amount, 100.0)
        self._pay(invoice, 1000.0)
        order.invalidate_recordset(['distribution_commission_amount'])
        self.assertEqual(order.distribution_commission_amount, 200.0)

    def test_base_of_partial_payment(self):
        # CA-21
        order = self._create_order()
        self._pay(self._invoice(order), 400.0)
        self.assertEqual(self._bases(order), [200.0])

    def test_base_after_credit_note(self):
        # CA-22, CA-24
        order = self._create_order()
        invoice = self._invoice(order)
        self._pay(invoice, 1000.0)
        refund = self._create_refund(invoice, order.order_line[0], [200.0])
        (self._receivable(refund) + self._receivable(invoice)).reconcile()
        self.assertEqual(self._bases(order), [500.0])
        self._pay(invoice, 800.0)
        self.assertEqual(self._bases(order), [500.0, 300.0])

    def test_base_of_bank_transaction(self):
        # CA-23
        order = self._create_order()
        invoice = self._invoice(order)
        self.pay_with_statement_line(invoice, self.bank_journal.id, fields.Date.today(), 1000.0)
        self.assertEqual(self._bases(order), [500.0])

    def test_base_of_payment_over_two_invoices(self):
        # CA-25: the amount is the one reconciled with F1, not the payment's.
        order = self._create_order()
        invoice = self._invoice(order)
        order_2 = self._create_order(lines=[(self.product_license, 1000.0)], project=self.project_2)
        invoice_2 = self._invoice(order_2)
        self._pay(invoice_2, 500.0)
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.partner_a.id,
            'amount': 1500.0, 'journal_id': self.bank_journal.id,
        })
        payment.action_post()
        self._reconcile(payment, invoice_2)
        self._reconcile(payment, invoice)
        self.assertEqual(self._bases(order), [500.0])

    def test_payment_without_distributed_lines(self):
        # CA-26, CA-37, CA-42
        order = self._create_order(lines=[(self.product_license, 1000.0)], project=self.project_2)
        self._pay(self._invoice(order), 1000.0)
        self.assertFalse(self._bases(order))
        self.assertFalse(self._provision_moves(order))
        self.assertFalse(self._commission_purchases(order))

    # R2.3
    def test_credit_note_is_not_a_payment(self):
        # CA-27
        order = self._create_order()
        invoice = self._invoice(order)
        refund = self._create_refund(invoice, order.order_line[0], [200.0])
        (self._receivable(refund) + self._receivable(invoice)).reconcile()
        self.assertFalse(self._bases(order))
        self.assertFalse(self._provision_moves(order))
        self.assertFalse(self._commission_purchases(order))

    # R3.2
    def test_commission_purchases(self):
        # CA-35
        order = self._create_order()
        invoice = self._invoice(order)
        payment = self._pay(invoice, 1000.0)
        purchases = self._commission_purchases(order)
        self.assertEqual(len(purchases), 2)
        for user, amount in ((self.user_v1, 60.0), (self.user_v2, 40.0)):
            line = self._purchase_of(order, user).order_line
            self.assertEqual(line.price_subtotal, amount)
            self.assertEqual(line.analytic_distribution, {str(self.analytic_1.id): 100.0})
            self.assertEqual(line.qty_received, line.product_qty)
            self.assertEqual(line.order_id.state, 'draft')
            self.assertIn(payment.name, line.name)
            self.assertIn("1,000.00", line.name)
            self.assertIn(invoice.name, line.name)

    def test_commission_purchase_single_salesperson(self):
        # CA-36
        order = self._create_order(salespeople=[(self.user_v1, 100.0)])
        self._pay(self._invoice(order), 1000.0)
        self.assertEqual(self._commission_purchases(order).mapped('amount_untaxed'), [100.0])

    # R4
    def test_provision_entry(self):
        # CA-38 to CA-41
        order = self._create_order()
        self._pay(self._invoice(order), 1000.0)
        move = self._provision_moves(order)
        self.assertEqual(len(move), 1)
        self.assertEqual(move.state, 'posted')
        lines = move.line_ids
        v1, v2 = self.user_v1.partner_id, self.user_v2.partner_id
        self.assertRecordValues(lines.sorted(lambda aml: (aml.account_id.code, aml.partner_id.name or '', aml.debit)), [
            {'account_id': self.commission_provision.id, 'partner_id': v1.id, 'debit': 0.0, 'credit': 60.0},
            {'account_id': self.commission_provision.id, 'partner_id': v2.id, 'debit': 0.0, 'credit': 40.0},
            {'account_id': self.implementation_provision.id, 'partner_id': False, 'debit': 0.0, 'credit': 375.0},
            {'account_id': self.implementation_expense.id, 'partner_id': False, 'debit': 375.0, 'credit': 0.0},
            {'account_id': self.commission_expense.id, 'partner_id': v1.id, 'debit': 60.0, 'credit': 0.0},
            {'account_id': self.commission_expense.id, 'partner_id': v2.id, 'debit': 40.0, 'credit': 0.0},
        ])
        self.assertTrue(all(line.analytic_distribution == {str(self.analytic_1.id): 100.0} for line in lines))
        self.assertEqual(sum(lines.mapped('debit')), 475.0)
        self.assertEqual(self._provision(self.implementation_provision, self.analytic_1), 375.0)
        self.assertEqual(self._provision(self.commission_provision, self.analytic_1, v1), 60.0)
        self.assertEqual(self._provision(self.commission_provision, self.analytic_1, v2), 40.0)

    def test_provision_on_behalf_of_commercial_entity(self):
        # CA-58.1 (R4.1): the purchase goes to V2's contact, the provision to its company.
        company_v2 = self.env['res.partner'].create({'name': "EV2", 'is_company': True})
        self.user_v2.partner_id.parent_id = company_v2
        order = self._create_order()
        self._pay(self._invoice(order), 1000.0)
        self.assertEqual(self._purchase_of(order, self.user_v2).amount_untaxed, 40.0)
        self.assertEqual(self._provision(self.commission_provision, self.analytic_1, company_v2), 40.0)
        bill = self._bill(company_v2, [(self.commission_provision, 40.0, [(self.analytic_1, 100.0)])])
        self.assertEqual(bill.state, 'posted')

    # Extra cases of version 2.1
    def test_invoice_of_two_orders_with_two_schemes(self):
        scheme_2 = self.scheme.copy({'name': "15/80/5", 'is_default': False})
        scheme_2.write({'rule_ids': [
            (1, scheme_2._get_rule('salesperson').id, {'percentage': 15.0}),
            (1, scheme_2._get_rule('implementer').id, {'percentage': 80.0}),
        ]})
        order_1 = self._create_order(lines=[(self.product_implementation, 1000.0)])
        order_2 = self._create_order(
            lines=[(self.product_implementation, 1000.0)], project=self.project_2, distribution_id=scheme_2.id,
        )
        (order_1 | order_2).action_confirm()
        invoice = (order_1 | order_2)._create_invoices()
        invoice.invoice_date = fields.Date.today()
        invoice.action_post()
        self._pay(invoice, 1000.0)
        self.assertEqual(self._bases(order_1), [500.0])
        self.assertEqual(self._bases(order_2), [500.0])
        self.assertEqual(self._provision(self.implementation_provision, self.analytic_1), 375.0)
        self.assertEqual(self._provision(self.implementation_provision, self.analytic_2), 400.0)
        self.assertEqual(sorted(self._commission_purchases(order_2).mapped('amount_untaxed')), [30.0, 45.0])

    def test_payment_provisioned_once(self):
        order = self._create_order()
        invoice = self._invoice(order)
        self._pay(invoice, 1000.0)
        partial = self.env['sale.distribution.accrual'].search([('order_id', '=', order.id)]).partial_reconcile_id
        partial._distribution_provision()
        self.assertEqual(self._bases(order), [500.0])
        self.assertEqual(len(self._provision_moves(order)), 1)

    def test_partial_payments_add_up_to_the_cent(self):
        order = self._create_order(lines=[(self.product_implementation, 1000.0), (self.product_license, 500.0)])
        invoice = self._invoice(order)
        for amount in (500.0, 500.0, 500.0):
            self._pay(invoice, amount)
        bases = self._bases(order)
        self.assertEqual(len(bases), 3)
        self.assertAlmostEqual(sum(bases), 1000.0, places=2)
