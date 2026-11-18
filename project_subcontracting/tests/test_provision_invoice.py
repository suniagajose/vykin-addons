from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ProvisionCommon


@tagged('post_install', '-at_install')
class TestProvisionInvoice(ProvisionCommon):
    """ Acceptance cases CA-10 to CA-19 of the spec (R2). """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.line_consulting = {'product': cls.consulting, 'price': 10000.0, 'analytic': {cls.analytic_x.id: 100}}
        cls.line_license = {'product': cls.license, 'price': 2000.0, 'analytic': {cls.analytic_y.id: 100}}
        cls.line_equipment = {'product': cls.equipment, 'price': 1000.0}

    def _percents(self, move):
        return [(line.provision_commission_percent, line.provision_implementation_percent) for line in move.invoice_line_ids]

    def test_10_percentages_are_kept_when_posting(self):
        invoice = self._invoice([self.line_consulting, self.line_license, self.line_equipment], salesperson=self.ana)
        self.assertEqual(self._percents(invoice), [(0, 0)] * 3)
        invoice.action_post()
        self.assertEqual(self._percents(invoice), [(20, 75), (20, 0), (0, 0)])

    def test_11_percentages_do_not_follow_later_changes(self):
        invoice = self._invoice([self.line_consulting, self.line_license, self.line_equipment], salesperson=self.ana, post=True)
        self.company_a.write({'provision_commission_percent': 10.0, 'provision_implementation_percent': 60.0})
        self.consulting.categ_id = self.categ_hardware
        self.categ_licenses.property_provision_commission_categ_id = False
        self.assertEqual(self._percents(invoice), [(20, 75), (20, 0), (0, 0)])

    def test_12_percentages_of_the_company_of_the_invoice(self):
        company_b = self.company_b
        template_b = self.env['project.project'].with_company(company_b).create(
            {'name': "PT-SUB B", 'is_template': True, 'allow_subcontracting': True, 'company_id': company_b.id},
        )
        provision_b = self.env['account.account'].with_company(company_b).search([
            ('provision_type', '=', 'commission'), ('company_ids', 'in', company_b.id),
        ], limit=1)
        implementation_b = self.env['account.account'].with_company(company_b).search([
            ('provision_type', '=', 'implementation'), ('company_ids', 'in', company_b.id),
        ], limit=1)
        company_b.write({'provision_commission_percent': 10.0, 'provision_implementation_percent': 60.0})
        # In B, as in A, the tracking comes before the accounts of the category.
        (self.consulting | self.partners_advice).with_company(company_b).write(
            {'service_tracking': 'task_in_project', 'project_template_id': template_b.id},
        )
        self.categ_services.with_company(company_b).write({
            'property_provision_commission_categ_id': provision_b.id,
            'property_provision_implementation_categ_id': implementation_b.id,
        })
        analytic = self.env['account.analytic.account'].create({
            'name': "AA-B", 'plan_id': self.analytic_x.plan_id.id, 'company_id': company_b.id,
        })
        invoice = self.env['account.move'].with_company(company_b).create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_a.id,
            'invoice_user_id': self.ana.id,
            'invoice_date': '2026-11-03',
            'journal_id': self.company_b_data['default_journal_sale'].id,
            'invoice_line_ids': [Command.create({
                'product_id': self.consulting.product_variant_id.id, 'quantity': 1, 'price_unit': 1000.0,
                'tax_ids': [Command.clear()], 'analytic_distribution': {analytic.id: 100},
            })],
        })
        invoice.action_post()
        self.assertEqual(self._percents(invoice), [(10, 60)])

    def _credit_note(self, lines, origin=None, **vals):
        return self._invoice(lines, move_type='out_refund', salesperson=self.ana, reversed_entry_id=origin.id if origin else False, **vals)

    def test_13_credit_note_takes_the_percentages_of_the_sale_line(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({'product_id': self.consulting.product_variant_id.id, 'product_uom_qty': 1})],
        })
        sale_lines = order.order_line
        invoice = self._invoice([{**self.line_consulting, 'sale_lines': sale_lines}, self.line_license], salesperson=self.ana, post=True)
        # Paid, so that its credit note is not reconciled with it when posted (that is R3, R7).
        self._pay(invoice, None, '2026-11-05')
        self.company_a.write({'provision_commission_percent': 10.0, 'provision_implementation_percent': 60.0})
        credit_note = self._credit_note([{**self.line_consulting, 'price': 2000.0, 'sale_lines': sale_lines}], origin=invoice)
        credit_note.action_post()
        self.assertEqual(self._percents(credit_note), [(20, 75)])

    def test_14_credit_note_without_sale_line_takes_the_first_line_of_the_product(self):
        invoice = self._invoice([self.line_license], salesperson=self.ana, post=True)
        self._pay(invoice, None, '2026-11-05')
        self.company_a.write({'provision_commission_percent': 10.0, 'provision_implementation_percent': 60.0})
        credit_note = self._credit_note([{**self.line_license, 'price': 500.0}], origin=invoice)
        credit_note.action_post()
        self.assertEqual(self._percents(credit_note), [(20, 0)])

    def test_15_credit_note_without_a_line_to_follow(self):
        invoice = self._invoice([self.line_license], salesperson=self.ana, post=True)
        self._pay(invoice, None, '2026-11-05')
        self.company_a.write({'provision_commission_percent': 10.0, 'provision_implementation_percent': 60.0})
        without_origin = self._credit_note([{**self.line_consulting, 'price': 1000.0}])
        without_line = self._credit_note([{**self.line_consulting, 'price': 1000.0}], origin=invoice)
        (without_origin | without_line).action_post()
        self.assertEqual(self._percents(without_origin), [(10, 60)])
        self.assertEqual(self._percents(without_line), [(10, 60)])

    def test_16_line_without_analytic(self):
        invoice = self._invoice([{**self.line_consulting, 'analytic': False}, self.line_equipment], salesperson=self.ana)
        with self.assertRaises(UserError) as caught:
            invoice.action_post()
        self.assertIn("Consultoría", str(caught.exception))
        self.assertNotIn("Equipo", str(caught.exception))
        self.assertEqual(invoice.state, 'draft')

    def test_17_commission_without_salesperson(self):
        invoice = self._invoice([{**self.line_license, 'price': 1000.0}])
        with self.assertRaises(UserError) as caught:
            invoice.action_post()
        self.assertIn("Licencia", str(caught.exception))

    def test_18_implementation_alone_needs_no_salesperson(self):
        invoice = self._invoice([
            {'product': self.support, 'price': 1000.0, 'analytic': {self.analytic_x.id: 100}}, self.line_equipment,
        ])
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(self._percents(invoice), [(0, 75), (0, 0)])

    def test_19_credit_note_without_salesperson(self):
        credit_note = self._invoice([{**self.line_consulting, 'price': 500.0}], move_type='out_refund')
        with self.assertRaises(UserError) as caught:
            credit_note.action_post()
        self.assertIn("Consultoría", str(caught.exception))
