from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ProvisionCase


@tagged('post_install', '-at_install')
class TestProvisionBill(ProvisionCase):
    """ Acceptance cases CA-55 to CA-62 of the spec (R6), from state E1: F1 posted and half paid, which
    provisions 3,750.00 of implementation in AA-X and 1,000.00 and 200.00 of commission of Comisionistas SA
    in AA-X and AA-Y. """

    def _bill(self, partner, lines, move_type='in_invoice', post=False):
        """ A vendor bill with lines (account, price, analytic distribution). """
        bill = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': '2026-11-12',
            'invoice_line_ids': [Command.create({
                'name': "Line", 'account_id': account.id, 'quantity': 1, 'price_unit': price,
                'tax_ids': [Command.clear()], 'analytic_distribution': analytic or False,
            }) for account, price, analytic in lines],
        })
        if post:
            bill.action_post()
        return bill

    def _x(self, percent=100):
        return {self.analytic_x.id: percent}

    def test_55_bill_within_the_balance(self):
        self._e1()
        bill = self._bill(self.carla, [(self.implementation_provision, 3750.0, self._x())], post=True)
        self.assertEqual(bill.state, 'posted')

    def test_56_bill_over_the_balance(self):
        self._e1()
        for lines in ([3750.01], [2000.0, 1800.0]):
            with self.subTest(lines=lines), self.assertRaises(UserError), self.cr.savepoint():
                self._bill(self.carla, [(self.implementation_provision, price, self._x()) for price in lines], post=True)

    def test_57_balance_consumed_by_an_earlier_bill(self):
        self._e1()
        self._bill(self.carla, [(self.implementation_provision, 3100.0, self._x())], post=True)
        with self.assertRaises(UserError) as caught:
            self._bill(self.carla, [(self.implementation_provision, 2000.0, self._x())], post=True)
        self.assertIn("650.00", str(caught.exception))

    def test_57_bills_of_the_same_batch_count_for_each_other(self):
        self._e1()
        bills = self._bill(self.carla, [(self.implementation_provision, 2000.0, self._x())]) \
            | self._bill(self.carla, [(self.implementation_provision, 1800.0, self._x())])
        with self.assertRaises(UserError):
            bills.action_post()

    def test_58_distribution_on_two_analytic_accounts(self):
        f3 = self._f3()
        self._pay(f3, 5800.0, '2026-11-11')
        split = {self.analytic_x.id: 50, self.analytic_y.id: 50}
        self._bill(self.carla, [(self.implementation_provision, 3000.0, split)], post=True)
        with self.assertRaises(UserError) as caught:
            self._bill(self.carla, [(self.implementation_provision, 3200.0, split)], post=True)
        self.assertIn("AA-Y", str(caught.exception))

    def test_59_receipt(self):
        self._e1()
        with self.assertRaises(UserError):
            self._bill(self.carla, [(self.implementation_provision, 1.0, {self.analytic_y.id: 100})], move_type='in_receipt', post=True)

    def test_60_commission_bill_of_the_reconciliation(self):
        self._e1()
        bill = self._commission_bills()
        bill.invoice_date = '2026-11-12'
        bill.action_post()
        self.assertEqual(bill.state, 'posted')
        with self.assertRaises(UserError):
            self._bill(self.ana.partner_id, [(self.commission_provision, 1000.01, self._x())], post=True)

    def test_61_commission_by_commercial_entity(self):
        self._e1()
        # Luis belongs to the entity of Ana: it has 1,000.00 in AA-X.
        self._bill(self.luis, [(self.commission_provision, 500.0, self._x())], post=True)
        with self.assertRaises(UserError):
            self._bill(self.beto.partner_id, [(self.commission_provision, 100.0, self._x())], post=True)

    def test_62_line_without_analytic(self):
        self._e1()
        for account in (self.implementation_provision, self.commission_provision):
            with self.subTest(account=account.code), self.assertRaises(UserError), self.cr.savepoint():
                self._bill(self.carla, [(account, 100.0, False)], post=True)
        bill = self._bill(self.carla, [(self.commission_expense, 100.0, False)], post=True)
        self.assertEqual(bill.state, 'posted')

    def test_credit_note_of_a_vendor_gives_the_provision_back(self):
        refund = self._bill(self.carla, [(self.implementation_provision, 100.0, self._x())], move_type='in_refund', post=True)
        self.assertEqual(refund.state, 'posted')
