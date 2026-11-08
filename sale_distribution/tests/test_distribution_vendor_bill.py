from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import DistributionCommon


@tagged('post_install', '-at_install')
class TestDistributionVendorBill(DistributionCommon):
    """ R6: vendor bills against a provision account, after PG1 (implementation 375 in A1,
    commissions V1 60 and V2 40). """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.order = cls._create_order()
        cls.invoice = cls._invoice(cls.order)
        cls.payment = cls._pay(cls.invoice, 1000.0)

    def _implementation_bill(self, amounts, post=True):
        return self._bill(self.implementer_1, [
            (self.implementation_provision, amount, analytics) for amount, analytics in amounts
        ], post=post)

    # R6.1
    def test_implementation_bill_within_provision(self):
        # CA-51, CA-52
        with self.assertRaises(UserError):
            self._implementation_bill([(375.01, [(self.analytic_1, 100.0)])])
        bill = self._implementation_bill([(375.0, [(self.analytic_1, 100.0)])])
        self.assertEqual(bill.state, 'posted')

    def test_implementation_receipt_within_provision(self):
        # R6 also checks purchase receipts.
        with self.assertRaises(UserError):
            self._bill(self.implementer_1, [(self.implementation_provision, 375.01, [(self.analytic_1, 100.0)])], move_type='in_receipt')

    def test_implementation_bill_lines_add_up(self):
        # CA-53, CA-54
        with self.assertRaises(UserError):
            self._implementation_bill([(200.0, [(self.analytic_1, 100.0)]), (175.01, [(self.analytic_1, 100.0)])])
        with self.assertRaises(UserError):
            self._implementation_bill([(375.0, [(self.analytic_1, 100.0)]), (0.01, [(self.analytic_2, 100.0)])])

    def test_implementation_bill_after_another(self):
        # CA-55
        self._implementation_bill([(300.0, [(self.analytic_1, 100.0)])])
        with self.assertRaises(UserError):
            self._implementation_bill([(75.01, [(self.analytic_1, 100.0)])])
        self.assertEqual(self._implementation_bill([(75.0, [(self.analytic_1, 100.0)])]).state, 'posted')

    def test_implementation_bill_on_two_analytic_accounts(self):
        # CA-56: A2 with 375 of balance; each analytic account counts its percentage.
        entry = self.env['account.move'].create({
            'move_type': 'entry',
            'date': fields.Date.today(),
            'line_ids': [
                Command.create({'account_id': self.implementation_expense.id, 'balance': 375.0,
                                'analytic_distribution': {str(self.analytic_2.id): 100.0}}),
                Command.create({'account_id': self.implementation_provision.id, 'balance': -375.0,
                                'analytic_distribution': {str(self.analytic_2.id): 100.0}}),
            ],
        })
        entry.action_post()
        split = [(self.analytic_1, 50.0), (self.analytic_2, 50.0)]
        with self.assertRaises(UserError):
            self._implementation_bill([(800.0, split)])
        self.assertEqual(self._implementation_bill([(600.0, split)]).state, 'posted')

    # R6.2
    def test_commission_bill_by_salesperson(self):
        # CA-57, CA-58
        v1, v2 = self.user_v1.partner_id, self.user_v2.partner_id
        with self.assertRaises(UserError):
            self._bill(v1, [(self.commission_provision, 60.01, [(self.analytic_1, 100.0)])])
        with self.assertRaises(UserError):
            self._bill(v2, [(self.commission_provision, 60.0, [(self.analytic_1, 100.0)])])
        self.assertEqual(self._bill(v1, [(self.commission_provision, 60.0, [(self.analytic_1, 100.0)])]).state, 'posted')
        self.assertEqual(self._bill(v2, [(self.commission_provision, 40.0, [(self.analytic_1, 100.0)])]).state, 'posted')

    def test_commission_bill_from_purchase(self):
        # The bill of the commission purchase consumes the salesperson's provision.
        bill = self._commission_bill(self._purchase_of(self.order, self.user_v1))
        self.assertEqual(bill.invoice_line_ids.account_id, self.commission_provision)
        self.assertEqual(self._provision(self.commission_provision, self.analytic_1, self.user_v1.partner_id), 0.0)

    # R6.3
    def test_bill_without_analytic_account(self):
        # CA-59
        with self.assertRaises(UserError):
            self._implementation_bill([(10.0, False)])
