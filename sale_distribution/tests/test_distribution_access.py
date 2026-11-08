from odoo import Command
from odoo.tests import tagged

from .common import DistributionCommon


@tagged('post_install', '-at_install')
class TestDistributionAccess(DistributionCommon):

    # R8.1
    def test_analytic_report(self):
        # CA-72
        order = self._create_order()
        self._pay(self._invoice(order), 1000.0)
        self._bill(self.implementer_1, [(self.implementation_provision, 300.0, [(self.analytic_1, 100.0)])])
        order_2 = self._create_order(lines=[(self.product_license, 1000.0)], project=self.project_2)
        self._pay(self._invoice(order_2), 1000.0)
        action = order.action_view_distribution_analytic()
        self.assertEqual(action['context']['group_by'], ['general_account_id'])
        lines = self.env['account.analytic.line'].search(action['domain'])
        self.assertTrue(lines)
        self.assertEqual(lines.account_id, self.analytic_1)
        amounts = dict(self.env['account.analytic.line']._read_group(
            action['domain'], ['general_account_id'], ['amount:sum'],
        ))
        income = order.order_line[0].product_id.property_account_income_id or self.company_data['default_account_revenue']
        self.assertEqual(amounts[income], 2000.0)
        self.assertEqual(amounts[self.commission_expense], -100.0)
        self.assertEqual(amounts[self.implementation_expense], -375.0)
        self.assertEqual(amounts[self.commission_provision], 100.0)
        self.assertEqual(amounts[self.implementation_provision], 75.0)

    # R8.2
    def test_multi_company(self):
        # CA-73, CA-74
        order = self._create_order()
        self._pay(self._invoice(order), 1000.0)
        company_2 = self.setup_other_company()['company']
        self.env.user.company_ids |= company_2
        user_1 = self._create_user('u1', "U1", 'sales_team.group_sale_manager,purchase.group_purchase_user')
        user_2 = self._create_user(
            'u2', "U2", 'sales_team.group_sale_manager,purchase.group_purchase_user', companies=self.company | company_2,
        )
        accrual_2 = self.env['sale.distribution.accrual'].sudo().create({
            'partial_reconcile_id': self.env['sale.distribution.accrual'].search([], limit=1).partial_reconcile_id.id,
            'invoice_id': self.init_invoice('out_invoice', company=company_2, amounts=[100.0], post=True).id,
            'order_id': self.env['sale.order'].with_company(company_2).create({'partner_id': self.partner_a.id}).id,
            'distribution_id': self.env['sale.distribution'].search([('company_id', '=', company_2.id)], limit=1).id,
            'date': '2026-01-01',
            'collected_untaxed_amount': 100.0,
        })
        purchase_2 = self.env['purchase.order'].with_company(company_2).create({
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({'product_id': self.product_a.id, 'product_qty': 1.0, 'price_unit': 1.0})],
        })
        Accrual, Purchase = self.env['sale.distribution.accrual'], self.env['purchase.order']
        self.assertEqual(Accrual.with_user(user_1).search([]).company_id, self.company)
        self.assertNotIn(purchase_2, Purchase.with_user(user_1).search([]))
        both = {'allowed_company_ids': (self.company | company_2).ids}
        self.assertIn(accrual_2, Accrual.with_user(user_2).with_context(**both).search([]))
        self.assertIn(purchase_2, Purchase.with_user(user_2).with_context(**both).search([]))
        only_1 = {'allowed_company_ids': self.company.ids}
        self.assertNotIn(accrual_2, Accrual.with_user(user_2).with_context(**only_1).search([]))
        self.assertNotIn(purchase_2, Purchase.with_user(user_2).with_context(**only_1).search([]))
