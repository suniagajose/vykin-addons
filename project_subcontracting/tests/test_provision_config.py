from ast import literal_eval

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.project_subcontracting import _setup_companies

from .common import ProvisionCommon


@tagged('post_install', '-at_install')
class TestProvisionConfig(ProvisionCommon):
    """ Acceptance cases CA-01 to CA-09 and CA-79 to CA-92 of the spec (R1). The invoice and payment
    parts of CA-03 to CA-05 and CA-09 are covered with the provision of the payment. """

    def _selectable(self, record, fname):
        domain = literal_eval(record._fields[fname].domain)
        return self.env['account.account'].search(domain)

    def test_01_selectors_offer_their_type(self):
        commission = self._selectable(self.consulting, 'property_provision_commission_id')
        self.assertIn(self.commission_provision, commission)
        self.assertIn(self.partners_provision, commission)
        self.assertNotIn(self.implementation_provision, commission)
        self.assertNotIn(self.other_liability, commission)
        self.assertNotIn(self.commission_expense, commission)
        self.assertEqual(
            self._selectable(self.categ_licenses, 'property_provision_implementation_categ_id') & self.company_a_accounts(),
            self.implementation_provision,
        )

    def company_a_accounts(self):
        return self.env['account.account'].search([('company_ids', 'in', self.company_a.id)])

    def test_03_resolution_through_the_categories(self):
        # Consultoría has none of its own: it takes those of Servicios, two levels up.
        self.assertEqual(self.consulting._get_provision_account('commission'), self.commission_provision)
        self.assertEqual(self.consulting._get_provision_account('implementation'), self.implementation_provision)
        accounts = self.consulting._get_product_accounts()
        self.assertEqual(accounts['provision_commission'], self.commission_provision)

    def test_04_own_account_wins_over_the_category(self):
        self.assertEqual(self.partners_advice._get_provision_account('commission'), self.partners_provision)
        self.assertEqual(self.partners_advice._get_provision_account('implementation'), self.implementation_provision)

    def test_05_no_account_no_role(self):
        self.assertFalse(self.support._get_provision_account('commission'))
        self.assertEqual(self.support._get_provision_account('implementation'), self.implementation_provision)
        self.assertFalse(self.equipment._has_provision_account())
        self.assertTrue(self.license._has_provision_account())
        self.assertFalse(self.license._get_provision_account('implementation'))

    def test_06_percentages_that_fit(self):
        self.company_a.write({'provision_commission_percent': 25.0, 'provision_implementation_percent': 75.0})
        self.company_a.write({'provision_commission_percent': 0.0, 'provision_implementation_percent': 0.0})

    def test_07_percentages_that_do_not_fit(self):
        self.company_a.write({'provision_commission_percent': 20.0, 'provision_implementation_percent': 75.0})
        for commission, implementation in ((30.0, 75.0), (-5.0, 75.0), (20.0, -1.0)):
            with self.subTest(commission=commission, implementation=implementation), mute_logger('odoo.sql_db'):
                with self.assertRaises(ValidationError), self.cr.savepoint():
                    self.company_a.write({
                        'provision_commission_percent': commission, 'provision_implementation_percent': implementation,
                    })
        self.assertEqual(
            (self.company_a.provision_commission_percent, self.company_a.provision_implementation_percent), (20.0, 75.0),
        )

    def test_08_setup_of_the_companies(self):
        for company in (self.company_a, self.company_b):
            with self.subTest(company=company.name):
                company._setup_provisions()  # again: nothing is duplicated
                Account = self.env['account.account'].with_company(company)
                for code, provision_type in (('205.02.02', 'commission'), ('205.02.03', 'implementation'), ('601.74.01', False), ('501.02.02', False)):
                    account = Account.search([*Account._check_company_domain(company), ('code', '=', code)])
                    self.assertEqual(len(account), 1, code)
                    self.assertEqual(account.provision_type, provision_type, code)
                self.assertEqual(company.provision_commission_expense_account_id.with_company(company).code, '601.74.01')
                self.assertEqual(company.provision_implementation_expense_account_id.with_company(company).code, '501.02.02')
                self.assertTrue(company.provision_journal_id.restrict_mode_hash_table)
                self.assertEqual(company.provision_journal_id.name, "Repartos")
                self.assertEqual(
                    (company.provision_commission_percent, company.provision_implementation_percent), (20.0, 75.0),
                )
                commission_product = self.env.ref('project_subcontracting.product_commission').with_company(company)
                hours_product = self.env.ref('project_subcontracting.product_subcontracted_hours').with_company(company)
                self.assertEqual(commission_product.property_account_expense_id.code, '205.02.02')
                self.assertEqual(hours_product.property_account_expense_id.code, '205.02.03')

    def test_08_install_reuses_existing_accounts(self):
        """ CA-08 (R1.4): the install hook reuses 601.74.01 and 205.02.02 when a company already has them,
        keeping their name and giving the provision its type, and creates what is missing. """
        company = self.env['res.company'].create({'name': "Compañía C", 'chart_template': self.company_a.chart_template})
        self.env.user.company_ids |= company
        Account = self.env['account.account'].with_company(company)
        expense, provision = Account.create([
            {'code': code, 'name': name, 'account_type': account_type, 'company_ids': [Command.set(company.ids)]}
            for code, name, account_type in (
                ('601.74.01', "Comisiones pagadas", 'expense'),
                ('205.02.02', "Acreedores por comisiones", 'liability_current'),
            )
        ])
        _setup_companies(self.env)
        self.assertEqual(company.provision_commission_expense_account_id, expense)
        self.assertEqual((expense.name, provision.name), ("Comisiones pagadas", "Acreedores por comisiones"))
        self.assertEqual(provision.provision_type, 'commission')
        commission_product = self.env.ref('project_subcontracting.product_commission').with_company(company)
        self.assertEqual(commission_product.property_account_expense_id, provision)
        implementation = Account.search([*Account._check_company_domain(company), ('code', '=', '205.02.03')])
        self.assertEqual(implementation.provision_type, 'implementation')
        self.assertEqual(company.provision_implementation_expense_account_id.with_company(company).code, '501.02.02')
        self.assertTrue(company.provision_journal_id.restrict_mode_hash_table)
        self.assertEqual((company.provision_commission_percent, company.provision_implementation_percent), (20.0, 75.0))

    def test_09_nothing_is_assigned_by_default(self):
        """ CA-09 (R1.4): the install assigns no provision account, so a product of a category the
        company did not configure has no share: no entry and no commission bill when it is paid. """
        Category, Product = self.env['product.category'], self.env['product.template']
        self.assertEqual(Category.search([
            '|', ('property_provision_commission_categ_id', '!=', False),
            ('property_provision_implementation_categ_id', '!=', False),
        ]), self.categ_services | self.categ_licenses)
        self.assertEqual(Product.search([
            '|', ('property_provision_commission_id', '!=', False), ('property_provision_implementation_id', '!=', False),
        ]), self.partners_advice | self.support)
        product = self._create_provision_product("Asesoría", Category.create({'name': "Otros"}), 'no')
        invoice = self._invoice(
            [{'product': product, 'price': 1000.0, 'analytic': {self.analytic_x.id: 100}}],
            salesperson=self.ana, post=True,
        )
        self.assertEqual(
            (invoice.invoice_line_ids.provision_commission_percent, invoice.invoice_line_ids.provision_implementation_percent),
            (0.0, 0.0),
        )
        self._pay(invoice, None, '2026-11-10')
        self.assertFalse(self._provision_entries())
        self.assertFalse(self._commission_bills())

    def test_settings_of_another_company(self):
        """ The provision journal and expense accounts belong to the company or to its parent. """
        with self.assertRaises(ValidationError):
            self.company_b.provision_journal_id = self.company_a.provision_journal_id
        with self.assertRaises(ValidationError):
            self.company_b.provision_commission_expense_account_id = self.company_a.provision_commission_expense_account_id

    def test_provision_journal_needs_a_hash(self):
        journal = self.env['account.journal'].create({
            'name': "Plain", 'code': 'PLN', 'type': 'general', 'company_id': self.company_a.id,
        })
        with self.assertRaises(ValidationError):
            self.company_a.provision_journal_id = journal

    # R1.5: a product with a provision generates its project from a template with Subcontracting

    def test_79_tracking_is_kept_by_a_product_with_provision(self):
        with self.assertRaises(ValidationError):
            self.license.write({'service_tracking': 'task_global_project', 'project_id': self.project_g.id})
        self.assertEqual((self.license.service_tracking, self.license.project_template_id), ('task_in_project', self.pt_sub))

    def test_80_template_of_a_product_with_inherited_provision(self):
        with self.assertRaises(ValidationError):
            self.consulting.project_template_id = self.pt_std
        with self.assertRaises(ValidationError):
            self.consulting.project_template_id = False

    def test_81_other_tracking_with_subcontracting_template(self):
        self.license.write({'service_tracking': 'project_only', 'project_template_id': self.pt_sub.id})
        self.license.write({'service_tracking': 'task_in_project', 'project_template_id': self.pt_sub2.id})

    def test_82_product_with_own_provision_keeps_tracking(self):
        with self.assertRaises(ValidationError):
            self.support.write({'service_tracking': 'no', 'project_template_id': False})

    def test_83_new_products(self):
        with self.assertRaises(ValidationError):
            self._create_provision_product("Licencia 2", self.categ_licenses, 'no')
        self._create_provision_product("Licencia 3", self.categ_licenses, 'task_in_project', self.pt_sub)
        self._create_provision_product("Diseño 2", self.categ_hardware, 'project_only', self.pt_std)
        # Their expense account is a provision, but they have none of their own.
        self.env.ref('project_subcontracting.product_commission').write({'name': "Comisión de venta"})
        self.env.ref('project_subcontracting.product_subcontracted_hours').write({'name': "Horas subcontratadas"})

    def test_84_category_assignment_names_the_products(self):
        with self.assertRaises(ValidationError) as caught:
            self.categ_hardware.property_provision_commission_categ_id = self.commission_provision
        message = str(caught.exception)
        for name in ("Equipo", "Diseño", "Instalación", "Mouse"):
            self.assertIn(name, message)
        self.assertNotIn("Soporte", message)

    def test_85_category_with_compliant_products(self):
        self.categ_training.property_provision_implementation_categ_id = self.implementation_provision

    def test_86_template_in_use_keeps_subcontracting(self):
        for vals in ({'allow_subcontracting': False}, {'allow_timesheets': False}, {'active': False}):
            with self.subTest(vals=vals), self.assertRaises(ValidationError) as caught:
                self.pt_sub2.write(vals)
            self.assertIn("Soporte", str(caught.exception))
        self.assertTrue(self.pt_sub2.allow_subcontracting)
        with self.assertRaises(ValidationError):
            self.pt_sub2.unlink()

    def test_87_template_not_in_use(self):
        self.pt_sub3.allow_subcontracting = False
        self.assertFalse(self.pt_sub3.allow_subcontracting)

    # R1.5.2: the project of the order must have Subcontracting for the products with provision

    def _order(self, project, *products):
        return self.env['sale.order'].create({
            'partner_id': self.partner_a.id,
            'project_id': project.id if project else False,
            'order_line': [Command.create({'product_id': product.product_variant_id.id}) for product in products],
        })

    def test_88_order_to_a_project_without_subcontracting(self):
        order = self._order(self.project_z, self.consulting)
        with self.assertRaises(UserError) as caught:
            order.action_confirm()
        self.assertIn("Z", str(caught.exception))
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.tasks_ids)

    def test_89_order_to_a_project_with_subcontracting(self):
        order = self._order(self.project_x, self.consulting)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_90_order_without_project(self):
        order = self._order(False, self.consulting)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertTrue(order.project_id.allow_subcontracting)

    def test_91_order_to_a_project_without_subcontracting_and_no_provision(self):
        order = self._order(self.project_z, self.equipment, self.design, self.installation)
        order.action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_92_order_with_only_one_provision_role(self):
        for product in (self.support, self.license):
            with self.subTest(product=product.name), self.assertRaises(UserError) as caught:
                self._order(self.project_z, product).action_confirm()
            self.assertIn("Z", str(caught.exception))
