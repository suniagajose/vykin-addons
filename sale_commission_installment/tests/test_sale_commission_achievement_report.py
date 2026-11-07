from odoo import Command, fields
from odoo.tests import tagged
from odoo.tools import SQL

from odoo.addons.sale_commission.tests.test_sale_commission_common import TestSaleCommissionCommon


@tagged('post_install', '-at_install')
class TestSaleCommissionAchievementReport(TestSaleCommissionCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The achievement of Amount Paid is dated on the reconciliation, which
        # happens now: the plan must cover the current year.
        today = fields.Date.today()
        cls.commission_plan_user.write({
            'date_from': today.replace(month=1, day=1),
            'date_to': today.replace(month=12, day=31),
            'type': 'achieve',
        })
        cls.commission_plan_user.action_approve()
        # Usual payment term of Vykin.
        cls.payment_term_4 = cls.env['account.payment.term'].create({
            'name': "50/25/15/10",
            'line_ids': [
                Command.create({'value': 'percent', 'value_amount': percent, 'nb_days': days})
                for percent, days in ((50, 0), (25, 30), (15, 60), (10, 90))
            ],
        })

    def _set_achievements(self, *rates):
        self.commission_plan_user.achievement_ids = self.env['sale.commission.plan.achievement'].create([{
            'type': achievement_type,
            'rate': rate,
            'plan_id': self.commission_plan_user.id,
        } for achievement_type, rate in rates])

    def _post_invoice(self, payment_term, quantity=1, price_unit=186000):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'user_id': self.commission_user_1.id,
            'payment_term_id': payment_term.id,
            'order_line': [Command.create({
                'product_id': self.commission_product_1.id,
                'product_uom_qty': quantity,
                'price_unit': price_unit,
                'tax_ids': [Command.clear()],
            })],
        })
        order.action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        return invoice

    def _pay_installments(self, invoice, count):
        # Pay each installment on its own, as from the installment line: every
        # payment fully reconciles its installment, which dates the achievement.
        term_lines = invoice.line_ids.filtered(lambda line: line.display_type == 'payment_term')
        for term_line in term_lines.sorted('date_maturity')[:count]:
            self.env['account.payment.register'].with_context(
                active_model='account.move.line',
                active_ids=term_line.ids,
            ).create({})._create_payments()

    def _get_achievements(self, invoice):
        report = self.env['sale.commission.achievement.report']
        report._pre_achievement_operation()
        return report.search([
            ('plan_id', '=', self.commission_plan_user.id),
            ('related_res_model', '=', 'account.move'),
            ('related_res_id', '=', invoice.id),
        ])

    def test_amount_paid_partial_payment_installments(self):
        self._set_achievements(('amount_paid', 0.2))
        invoice = self._post_invoice(self.payment_term_4)
        self.assertEqual(len(invoice.line_ids.filtered(lambda line: line.display_type == 'payment_term')), 4)

        # Only the first installment (50%) is paid.
        self._pay_installments(invoice, 1)

        achievements = self._get_achievements(invoice)
        self.assertEqual(invoice.payment_state, 'partial')
        self.assertAlmostEqual(sum(achievements.mapped('achieved')), 18600, places=2,
                               msg="0.2 * 186000 * 0.5 = 18600, not multiplied by the 4 installments")

    def test_amount_paid_date_latest_full_reconciliation(self):
        self._set_achievements(('amount_paid', 0.2))
        invoice = self._post_invoice(self.payment_term_4)
        self._pay_installments(invoice, 2)
        term_lines = invoice.line_ids.filtered(lambda line: line.display_type == 'payment_term').sorted('date_maturity')
        first_reconcile, last_reconcile = term_lines[0].full_reconcile_id, term_lines[1].full_reconcile_id
        self.assertTrue(first_reconcile and last_reconcile)
        # Both reconciliations happen in this transaction: date the first one a day
        # earlier, so picking any reconciliation but the latest gives another date.
        self.env.flush_all()
        self.env.cr.execute(SQL(
            "UPDATE account_full_reconcile SET write_date = write_date - INTERVAL '1 day' WHERE id = %s",
            first_reconcile.id,
        ))
        self.env.invalidate_all()

        achievements = self._get_achievements(invoice)
        self.assertTrue(achievements)
        self.assertNotEqual(first_reconcile.write_date.date(), last_reconcile.write_date.date())
        self.assertEqual(set(achievements.mapped('date')), {last_reconcile.write_date.date()})

    def test_amount_and_quantity_invoiced_installments(self):
        self._set_achievements(('amount_invoiced', 0.1), ('qty_invoiced', 5))
        invoice_4 = self._post_invoice(self.payment_term_4, quantity=2, price_unit=93000)
        invoice_1 = self._post_invoice(self.pay_terms_a, quantity=2, price_unit=93000)

        achieved_4 = sum(self._get_achievements(invoice_4).mapped('achieved'))
        achieved_1 = sum(self._get_achievements(invoice_1).mapped('achieved'))
        self.assertAlmostEqual(achieved_1, 0.1 * 186000 + 5 * 2, places=2)
        self.assertAlmostEqual(achieved_4, achieved_1, places=2,
                               msg="4 installments must give the same achievement as 1")
