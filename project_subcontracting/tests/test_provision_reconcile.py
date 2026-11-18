from odoo import Command
from odoo.tests import tagged

from .common import ProvisionCase


@tagged('post_install', '-at_install')
class TestProvisionReconcile(ProvisionCase):
    """ Acceptance cases CA-20 to CA-39 of the spec (R3 and R4), on its data: invoice F1 of company A and
    the payment P1 that reconciles half of it. """

    def test_20_entry_of_a_payment(self):
        self._e1()
        entry = self._provision_entries()
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.journal_id, self.company_a.provision_journal_id)
        self.assertEqual(str(entry.date), '2026-11-10')
        self.assertEqual(entry.state, 'posted')
        ax, ay = (self.analytic_x.id,), (self.analytic_y.id,)
        self.assertEqual(self._items(entry), sorted([
            ('601.74.01', 1000.0, 0.0, "Comisionistas SA", ax), ('205.02.02', 0.0, 1000.0, "Comisionistas SA", ax),
            ('501.02.02', 3750.0, 0.0, "", ax), ('205.02.03', 0.0, 3750.0, "", ax),
            ('601.74.01', 200.0, 0.0, "Comisionistas SA", ay), ('205.02.02', 0.0, 200.0, "Comisionistas SA", ay),
        ]))
        # Nothing for the company: only its six items, for 4,950.00.
        self.assertEqual(len(entry.line_ids), 6)
        self.assertEqual(sum(entry.line_ids.mapped('debit')), 4950.0)

    def test_11_entry_does_not_follow_later_changes(self):
        """ CA-11: the percentages and the provision accounts are those of the posted invoice. """
        self.company_a.write({'provision_commission_percent': 10.0, 'provision_implementation_percent': 60.0})
        self.consulting.categ_id = self.categ_hardware
        self.categ_licenses.property_provision_commission_categ_id = False
        self._pay(self.f1, 7540.0, '2026-11-10')
        ax, ay = (self.analytic_x.id,), (self.analytic_y.id,)
        self.assertEqual(self._items(self._provision_entries()), sorted([
            ('601.74.01', 1000.0, 0.0, "Comisionistas SA", ax), ('205.02.02', 0.0, 1000.0, "Comisionistas SA", ax),
            ('501.02.02', 3750.0, 0.0, "", ax), ('205.02.03', 0.0, 3750.0, "", ax),
            ('601.74.01', 200.0, 0.0, "Comisionistas SA", ay), ('205.02.02', 0.0, 200.0, "Comisionistas SA", ay),
        ]))

    def test_21_payment_from_a_bank_movement(self):
        f4 = self._invoice([{**self.line_y, 'price': 1000.0}], salesperson=self.ana, date='2026-11-05', post=True, taxed=True)
        bank = self.env['account.move'].create({
            'move_type': 'entry', 'date': '2026-11-12', 'journal_id': self.company_data['default_journal_bank'].id,
            'line_ids': [
                Command.create({'account_id': self.company_data['default_journal_bank'].default_account_id.id, 'debit': 1160.0}),
                Command.create({'account_id': self._receivable(f4).account_id.id, 'credit': 1160.0, 'partner_id': self.partner_a.id}),
            ],
        })
        bank.action_post()
        (self._receivable(f4) | self._receivable(bank)).reconcile()
        entry = self._provision_entries()
        self.assertEqual(str(entry.date), '2026-11-12')
        self.assertEqual(self._items(entry), sorted([
            ('601.74.01', 200.0, 0.0, "Comisionistas SA", (self.analytic_y.id,)),
            ('205.02.02', 0.0, 200.0, "Comisionistas SA", (self.analytic_y.id,)),
        ]))

    def test_22_credit_note_returned_to_the_customer(self):
        self._e1()
        nc2 = self._nc2()
        self._pay(nc2, 1160.0, '2026-11-15')
        entry = self._provision_entries()[-1]
        self.assertEqual(str(entry.date), '2026-11-15')
        ax = (self.analytic_x.id,)
        self.assertEqual(self._items(entry), sorted([
            ('205.02.02', 200.0, 0.0, "Comisionistas SA", ax), ('601.74.01', 0.0, 200.0, "Comisionistas SA", ax),
            ('205.02.03', 750.0, 0.0, "", ax), ('501.02.02', 0.0, 750.0, "", ax),
        ]))
        self.assertEqual(self._balance(self.commission_provision, self.analytic_x), 800.0)
        self.assertEqual(self._balance(self.implementation_provision, self.analytic_x), 3000.0)
        # CA-38: a negative net creates no commission bill.
        self.assertEqual(len(self._commission_bills()), 1)

    def test_23_credit_note_partly_returned(self):
        self._e1()
        nc2 = self._nc2()
        self._pay(nc2, 580.0, '2026-11-15')
        entry = self._provision_entries()[-1]
        self.assertEqual(
            [(code, debit, credit) for code, debit, credit, _partner, _analytic in self._items(entry)],
            sorted([('205.02.02', 100.0, 0.0), ('601.74.01', 0.0, 100.0), ('205.02.03', 375.0, 0.0), ('501.02.02', 0.0, 375.0)]),
        )

    def test_24_credit_note_reconciled_with_the_invoice(self):
        self._e1()
        before = self._provision_entries()
        # Posting a credit note with its invoice as origin reconciles it with that invoice.
        nc1 = self._nc1()
        entries = self._provision_entries() - before
        self.assertEqual(len(entries), 2)
        invoice_entry = entries.filtered(lambda entry: entry.provision_origin_move_id == self.f1)
        credit_entry = entries.filtered(lambda entry: entry.provision_origin_move_id == nc1)
        ax, ay = (self.analytic_x.id,), (self.analytic_y.id,)
        self.assertEqual(self._items(invoice_entry), sorted([
            ('601.74.01', 307.69, 0.0, "Comisionistas SA", ax), ('205.02.02', 0.0, 307.69, "Comisionistas SA", ax),
            ('501.02.02', 1153.85, 0.0, "", ax), ('205.02.03', 0.0, 1153.85, "", ax),
            ('601.74.01', 61.54, 0.0, "Comisionistas SA", ay), ('205.02.02', 0.0, 61.54, "Comisionistas SA", ay),
        ]))
        self.assertEqual(self._items(credit_entry), sorted([
            ('205.02.02', 400.0, 0.0, "Comisionistas SA", ax), ('601.74.01', 0.0, 400.0, "Comisionistas SA", ax),
            ('205.02.03', 1500.0, 0.0, "", ax), ('501.02.02', 0.0, 1500.0, "", ax),
        ]))
        # CA-32: both entries have the date of the most recent document.
        self.assertEqual({str(entry.date) for entry in entries}, {'2026-11-12'})
        self.assertAlmostEqual(self._balance(self.commission_provision, self.analytic_x), 907.69)
        self.assertAlmostEqual(self._balance(self.commission_provision, self.analytic_y), 261.54)
        self.assertAlmostEqual(self._balance(self.implementation_provision, self.analytic_x), 3403.85)
        # The net of the salesperson is negative in AA-X: its bill has a single line, AA-Y.
        bill = self._commission_bills()[-1]
        self.assertEqual(len(bill.invoice_line_ids), 2 - 1)
        self.assertEqual(
            [(line.analytic_distribution, line.price_unit) for line in bill.invoice_line_ids],
            [({str(self.analytic_y.id): 100.0}, 61.54)],
        )

    def test_25_foreign_currency_uses_the_portion_in_the_document_currency(self):
        usd = self.setup_other_currency('EUR', rates=[('2026-01-01', 0.05), ('2026-11-10', 1 / 21.0)])
        invoice = self._invoice(
            [{**self.line_y, 'price': 1000.0}], salesperson=self.beto, date='2026-11-03', post=True, taxed=True,
            currency_id=usd.id,
        )
        self.assertEqual(invoice.amount_total, 1160.0)
        self._pay(invoice, 580.0, '2026-11-10', currency=usd)
        entries = self._provision_entries().filtered(lambda entry: entry.provision_origin_move_id == invoice)
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            sorted((code, debit, credit) for code, debit, credit, _partner, _analytic in self._items(entries)),
            sorted([('601.74.01', 2000.0, 0.0), ('205.02.02', 0.0, 2000.0)]),
        )

    def test_26_distribution_on_two_analytic_accounts(self):
        f3 = self._f3()
        self._pay(f3, 5800.0, '2026-11-11')
        entry = self._provision_entries()
        self.assertEqual(len(entry.line_ids), 4)
        for item in entry.line_ids:
            self.assertEqual(item.analytic_distribution, {str(self.analytic_x.id): 60.0, str(self.analytic_y.id): 40.0})
        self.assertEqual(
            sorted((code, debit, credit, partner) for code, debit, credit, partner, _analytic in self._items(entry)),
            sorted([
                ('601.74.01', 1000.0, 0.0, "Beto Díaz"), ('205.02.02', 0.0, 1000.0, "Beto Díaz"),
                ('501.02.02', 3750.0, 0.0, ""), ('205.02.03', 0.0, 3750.0, ""),
            ]),
        )

    def test_27_the_last_payment_completes_the_line(self):
        self._e1()
        self._pay(self.f1, 7540.0, '2026-11-20')
        entry = self._provision_entries()[-1]
        self.assertEqual(
            sorted((code, debit, credit) for code, debit, credit, _partner, _analytic in self._items(entry)),
            sorted([
                ('601.74.01', 1000.0, 0.0), ('205.02.02', 0.0, 1000.0), ('501.02.02', 3750.0, 0.0), ('205.02.03', 0.0, 3750.0),
                ('601.74.01', 200.0, 0.0), ('205.02.02', 0.0, 200.0),
            ]),
        )
        self.assertEqual(self._provisioned(self.f1_x, 'commission'), 2000.0)
        self.assertEqual(self._provisioned(self.f1_x, 'implementation'), 7500.0)
        self.assertEqual(self._provisioned(self.f1_y, 'commission'), 400.0)

    def test_28_rounding_is_settled_by_the_last_payment(self):
        f2 = self._invoice(
            [{**self.line_x, 'price': 100.0}], salesperson=self.ana, date='2026-11-03', post=True, taxed=True,
        )
        for amount in (38.67, 38.67, 38.66):
            self._pay(f2, amount, '2026-11-10')
        entries = self._provision_entries()
        commission = [sum(entry.line_ids.filtered(lambda i: i.account_id == self.commission_provision).mapped('credit')) for entry in entries]
        implementation = [sum(entry.line_ids.filtered(lambda i: i.account_id == self.implementation_provision).mapped('credit')) for entry in entries]
        self.assertEqual(commission, [6.67, 6.67, 6.66])
        self.assertEqual(implementation, [25.0, 25.0, 25.0])

    def test_29_last_payment_after_a_credit_note(self):
        self._e1()
        self._nc1()
        before = self._provision_entries()
        self._pay(self.f1, 5220.0, '2026-11-20')
        entry = (self._provision_entries() - before)
        self.assertEqual(len(entry), 1)
        self.assertEqual(
            sorted((code, credit) for code, _debit, credit, _partner, _analytic in self._items(entry) if credit),
            sorted([('205.02.02', 692.31), ('205.02.03', 2596.15), ('205.02.02', 138.46)]),
        )
        self.assertAlmostEqual(self._provisioned(self.f1_x, 'commission'), 2000.0)
        self.assertAlmostEqual(self._provisioned(self.f1_x, 'implementation'), 7500.0)
        self.assertAlmostEqual(self._provisioned(self.f1_y, 'commission'), 400.0)

    def test_30_date_of_the_payment(self):
        self._e1()
        self.assertEqual(str(self._provision_entries().date), '2026-11-10')

    def test_31_date_in_a_locked_period(self):
        f5 = self._invoice([self.line_x | {'price': 1000.0}], salesperson=self.ana, date='2026-10-01', post=True, taxed=True)
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.partner_a.id,
            'amount': 1160.0, 'date': '2026-10-20',
        })
        payment.action_post()
        self.company_a.fiscalyear_lock_date = '2026-10-31'
        (self._receivable(f5) | self._receivable(payment.move_id)).reconcile()
        self.assertEqual(str(self._provision_entries().date), '2026-11-01')

    def test_34_nothing_to_provision(self):
        invoice = self._invoice([self.line_equipment], salesperson=self.ana, post=True, taxed=True)
        self._pay(invoice, 1160.0, '2026-11-10')
        self.assertFalse(self._provision_entries().filtered(lambda entry: entry.provision_origin_move_id == invoice))
        self.assertFalse(self._commission_bills())

    def test_35_commission_bill(self):
        self._e1()
        bill = self._commission_bills()
        self.assertEqual(len(bill), 1)
        self.assertEqual((bill.state, bill.partner_id), ('draft', self.ana.partner_id))
        self.assertEqual(bill.amount_total, 1200.0)
        self.assertEqual(
            sorted((tuple(line.analytic_distribution), line.price_unit, line.product_id) for line in bill.invoice_line_ids),
            sorted([
                ((str(self.analytic_x.id),), 1000.0, self.env.ref('project_subcontracting.product_commission')),
                ((str(self.analytic_y.id),), 200.0, self.env.ref('project_subcontracting.product_commission')),
            ]),
        )
        description = bill.invoice_line_ids[0].name
        self.assertIn(self.f1.name, description)
        self.assertIn("7,540.00", description)
        self.assertIn(self.f1.matched_payment_ids.move_id.display_name, description)

    def test_36_a_bill_per_payment(self):
        self._e1()
        first = self._commission_bills()
        self._pay(self.f1, 7540.0, '2026-11-20')
        second = self._commission_bills() - first
        self.assertEqual(len(second), 1)
        self.assertEqual(sorted(second.invoice_line_ids.mapped('price_unit')), [200.0, 1000.0])
        self.assertIn(self.f1.matched_payment_ids.sorted('id')[-1].move_id.display_name, second.invoice_line_ids[0].name)
        self.assertEqual(sorted(first.invoice_line_ids.mapped('price_unit')), [200.0, 1000.0])

    def test_37_bill_of_two_analytic_accounts(self):
        f3 = self._f3()
        self._pay(f3, 5800.0, '2026-11-11')
        bill = self._commission_bills()
        self.assertEqual(bill.partner_id, self.beto.partner_id)
        self.assertEqual(
            sorted((tuple(line.analytic_distribution), line.price_unit) for line in bill.invoice_line_ids),
            sorted([((str(self.analytic_x.id),), 600.0), ((str(self.analytic_y.id),), 400.0)]),
        )

    def test_39_implementation_only(self):
        invoice = self._invoice(
            [{'product': self.support, 'price': 1000.0, 'analytic': {self.analytic_x.id: 100}}],
            salesperson=self.ana, post=True, taxed=True,
        )
        self._pay(invoice, 1160.0, '2026-11-10')
        entry = self._provision_entries()
        self.assertEqual(
            sorted((code, debit, credit) for code, debit, credit, _partner, _analytic in self._items(entry)),
            sorted([('501.02.02', 750.0, 0.0), ('205.02.03', 0.0, 750.0)]),
        )
        self.assertFalse(self._commission_bills())
