from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import DistributionCommon


@tagged('post_install', '-at_install')
class TestDistributionScheme(DistributionCommon):

    def _create_scheme(self, percentages, company=None):
        roles = ('salesperson', 'implementer', 'company')
        return self.env['sale.distribution'].create({
            'name': "Prueba",
            'company_id': (company or self.company).id,
            'rule_ids': [
                Command.create({
                    'role': role,
                    'percentage': percentage,
                    'expense_account_id': self.scheme._get_rule(role).expense_account_id.id,
                    'payable_account_id': self.scheme._get_rule(role).payable_account_id.id,
                })
                for role, percentage in zip(roles, percentages)
            ],
        })

    def _paid_order(self):
        order = self._create_order()
        invoice = self._invoice(order)
        payment = self._pay(invoice, 1000.0)
        return order, invoice, payment

    # R1.1
    def test_scheme_total(self):
        # CA-01 to CA-04
        self.assertEqual(self._create_scheme([20.0, 75.0, 5.0]).total_percentage, 100.0)
        for percentages in ([20.0, 75.0, 4.0], [20.0, 75.0, 6.0]):
            with self.assertRaises(ValidationError):
                self._create_scheme(percentages)
        with self.assertRaises(ValidationError):
            self.env['sale.distribution'].create({'name': "Vacío"})

    def test_rule_accounts(self):
        # A rule takes both accounts or none, and the company rule takes none.
        other = self._create_scheme([20.0, 75.0, 5.0])
        with self.assertRaises(ValidationError):
            other._get_rule('implementer').payable_account_id = False
        with self.assertRaises(ValidationError):
            self.rule_company.expense_account_id = self.commission_expense

    # R1.2
    def test_default_scheme_by_company(self):
        # CA-05 to CA-07
        self.assertEqual(self._create_order().distribution_id, self.scheme)
        company_2 = self.setup_other_company()['company']
        scheme_2 = self.env['sale.distribution'].search([('company_id', '=', company_2.id), ('is_default', '=', True)])
        order_2 = self.env['sale.order'].with_company(company_2).create({'partner_id': self.partner_a.id})
        self.assertTrue(scheme_2)
        self.assertEqual(order_2.distribution_id, scheme_2)
        company_3 = self.env['res.company'].create({'name': "C3"})
        self.env.user.company_ids |= company_3
        order_3 = self.env['sale.order'].with_company(company_3).create({'partner_id': self.partner_a.id})
        self.assertFalse(order_3.distribution_id)

    # R1.3
    def test_scheme_locked_with_provision(self):
        # CA-08, CA-09, CA-10
        other = self._create_scheme([20.0, 75.0, 5.0])
        order = self._create_order()
        self._invoice(order)
        order.distribution_id = other
        order.distribution_id = self.scheme
        invoice = order.invoice_ids
        payment = self._pay(invoice, 1000.0)
        with self.assertRaises(UserError):
            order.distribution_id = other
        self._unreconcile(payment)
        with self.assertRaises(UserError):
            order.distribution_id = other
        self.assertEqual(order.distribution_id, self.scheme)

    # R1.4
    def test_rules_locked_with_provision(self):
        # CA-11 to CA-14
        self._paid_order()
        with self.assertRaises(UserError):
            self.scheme.write({'rule_ids': [
                Command.update(self.rule_salesperson.id, {'percentage': 25.0}),
                Command.update(self.rule_company.id, {'percentage': 0.0}),
            ]})
        with self.assertRaises(UserError):
            self.rule_implementer.role = 'salesperson'
        with self.assertRaises(UserError):
            self.rule_salesperson.expense_account_id = self.implementation_expense
        other = self._create_scheme([20.0, 75.0, 5.0])
        other.write({'rule_ids': [
            Command.update(other._get_rule('salesperson').id, {'percentage': 25.0}),
            Command.update(other._get_rule('implementer').id, {
                'percentage': 70.0, 'expense_account_id': self.commission_expense.id,
            }),
        ]})
        self.assertEqual(other._get_rule('implementer').percentage, 70.0)

    # R1.5
    def test_order_without_scheme(self):
        # CA-15
        order = self._create_order(lines=[(self.product_implementation, 1000.0)], distribution_id=False)
        invoice = self._invoice(order)
        self._pay(invoice, 1000.0)
        self.assertFalse(self._bases(order))
        self.assertFalse(self._provision_moves(order))
        self.assertFalse(self._commission_purchases(order))

    # R3.1
    def test_salespeople_proposed(self):
        # CA-28, CA-29, CA-30
        order = self._create_order(salespeople=[])
        self.assertEqual(order.distribution_salesperson_ids.mapped('user_id'), self.user_v1)
        self.assertEqual(order.distribution_salesperson_ids.percentage, 100.0)
        self._invoice(order)
        order.user_id = self.user_v2
        self.assertEqual(order.distribution_salesperson_ids.mapped('user_id'), self.user_v2)
        order.distribution_salesperson_ids = [Command.clear(), Command.create({'user_id': self.user_v1.id, 'percentage': 60.0}),
                                              Command.create({'user_id': self.user_v2.id, 'percentage': 40.0})]
        self.assertEqual(order.distribution_salesperson_ids.mapped('percentage'), [60.0, 40.0])

    # R3.1.1
    def test_salespeople_total(self):
        # CA-31, CA-32
        for percentages in ((60.0, 30.0), (60.0, 50.0)):
            with self.assertRaises(ValidationError):
                self._create_order(salespeople=[(self.user_v1, percentages[0]), (self.user_v2, percentages[1])])

    # R3.1.2
    def test_confirm_without_salespeople(self):
        # CA-33
        order = self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'user_id': False,
            'order_line': [Command.create({'product_id': self.product_implementation.id})],
        })
        self.assertFalse(order.distribution_salesperson_ids)
        with self.assertRaises(UserError):
            order.action_confirm()

    # R3.1.3
    def test_salespeople_changed_after_provision(self):
        # CA-34: later payments follow the new split; what was provisioned stays.
        order, invoice, _payment = self._paid_order()
        order.distribution_salesperson_ids.filtered(lambda s: s.user_id == self.user_v1).percentage = 50.0
        order.distribution_salesperson_ids.filtered(lambda s: s.user_id == self.user_v2).percentage = 50.0
        self.assertIn("V1 50%", order.message_ids[0].body)
        self._pay(invoice, 1000.0)
        first, second = self._provision_moves(order).sorted('id')
        commission_lines = first.line_ids.filtered(lambda aml: aml.account_id == self.commission_provision)
        self.assertEqual(sorted(commission_lines.mapped('credit')), [40.0, 60.0])
        commission_lines = second.line_ids.filtered(lambda aml: aml.account_id == self.commission_provision)
        self.assertEqual(commission_lines.mapped('credit'), [50.0, 50.0])
        purchases = self._commission_purchases(order)
        self.assertEqual(sorted(purchases.mapped('amount_untaxed')), [40.0, 50.0, 50.0, 60.0])

    def test_salesperson_unchanged_with_provision(self):
        # CA-34.1
        order, _invoice, _payment = self._paid_order()
        order.user_id = self.user_v2
        self.assertEqual(order.distribution_salesperson_ids.mapped('percentage'), [60.0, 40.0])

    # R3.1.4
    def test_salespeople_locked_for_salesperson(self):
        # CA-34.2
        order, _invoice, _payment = self._paid_order()
        with self.assertRaises(AccessError):
            order.with_user(self.user_v1).write({'distribution_salesperson_ids': [
                Command.clear(), Command.create({'user_id': self.user_v1.id, 'percentage': 100.0}),
            ]})
        # Nor with the context the order's write uses internally.
        with self.assertRaises(AccessError):
            order.with_user(self.user_v1).with_context(sale_distribution_tracking=True).write({
                'distribution_salesperson_ids': [Command.clear()],
            })
        # Nor by moving a salesperson of an order without provision to it.
        other = self._create_order(salespeople=[(self.user_v1, 100.0)])
        with self.assertRaises(AccessError):
            other.distribution_salesperson_ids.with_user(self.user_v1).write({'order_id': order.id})
        self.assertEqual(order.distribution_salesperson_ids.mapped('percentage'), [60.0, 40.0])
