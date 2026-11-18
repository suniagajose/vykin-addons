from odoo import Command, fields
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class ProvisionCommon(AccountTestInvoicingCommon):
    """ The common data of the acceptance cases of the provision of the payment (spec "Reparto del
    cobro"): the accounts, categories, products and project templates of company A and of company B. """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('project.group_project_manager')
            | cls.env.ref('sales_team.group_sale_manager')
            | cls.env.ref('analytic.group_analytic_accounting')
        )
        cls.company_a = cls.company_data['company']
        cls.company_b_data = cls.setup_other_company()
        cls.company_b = cls.company_b_data['company']
        cls.env.user.company_ids |= cls.company_b
        get_account = cls.company_a._get_provision_account_by_code
        cls.commission_provision = get_account('205.02.02', "Provisión de comisiones", 'liability_current', 'commission')
        cls.partners_provision = get_account('205.02.12', "Provisión de comisiones socios", 'liability_current', 'commission')
        cls.implementation_provision = get_account(
            '205.02.03', "Provisión de implementación", 'liability_current', 'implementation',
        )
        cls.other_liability = get_account('205.99.01', "Otros pasivos", 'liability_current')
        cls.commission_expense = cls.company_a.provision_commission_expense_account_id
        cls.implementation_expense = cls.company_a.provision_implementation_expense_account_id

        project_plan, _other_plans = cls.env['account.analytic.plan']._get_all_plans()
        cls.analytic_x, cls.analytic_y = cls.env['account.analytic.account'].create([
            {'name': name, 'plan_id': project_plan.id, 'company_id': False} for name in ("AA-X", "AA-Y")
        ])
        cls.ana = new_test_user(cls.env, login='ana', name="Ana Pérez", groups='sales_team.group_sale_salesman')
        cls.beto = new_test_user(cls.env, login='beto', name="Beto Díaz", groups='sales_team.group_sale_salesman')
        # Ana belongs to a company: the commission is provisioned on behalf of that entity.
        cls.commission_entity = cls.env['res.partner'].create({'name': "Comisionistas SA", 'is_company': True})
        cls.luis = cls.env['res.partner'].create({'name': "Luis Mora", 'parent_id': cls.commission_entity.id})
        cls.carla = cls.env['res.partner'].create({'name': "Carla Ruiz"})
        cls.ana.partner_id.parent_id = cls.commission_entity
        cls.tax_16 = cls.env['account.tax'].create({
            'name': "IVA 16%", 'amount': 16.0, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'company_id': cls.company_a.id,
        })

        # Project templates: with and without Subcontracting (the first three are alike).
        Project = cls.env['project.project']
        cls.pt_sub, cls.pt_sub2, cls.pt_sub3, cls.pt_std = Project.create([
            {'name': name, 'is_template': True, 'allow_subcontracting': subcontracting}
            for name, subcontracting in (("PT-SUB", True), ("PT-SUB2", True), ("PT-SUB3", True), ("PT-STD", False))
        ])
        cls.project_g, cls.project_x, cls.project_z = Project.create([
            {'name': "G", 'allow_subcontracting': False},
            {'name': "X", 'allow_subcontracting': True},
            {'name': "Z", 'allow_subcontracting': False},
        ])

        Category = cls.env['product.category']
        cls.categ_services = Category.create({'name': "Servicios"})
        cls.categ_implementation = Category.create({'name': "Implementación", 'parent_id': cls.categ_services.id})
        cls.categ_licenses = Category.create({'name': "Licencias"})
        cls.categ_hardware = Category.create({'name': "Hardware"})
        cls.categ_peripherals = Category.create({'name': "Periféricos", 'parent_id': cls.categ_hardware.id})
        cls.categ_training = Category.create({'name': "Capacitación"})

        cls.consulting = cls._create_provision_product("Consultoría", cls.categ_implementation, 'task_in_project', cls.pt_sub)
        cls.partners_advice = cls._create_provision_product(
            "Asesoría socios", cls.categ_services, 'task_in_project', cls.pt_sub,
            property_provision_commission_id=cls.partners_provision.id,
        )
        cls.license = cls._create_provision_product("Licencia", cls.categ_licenses, 'task_in_project', cls.pt_sub)
        cls.support = cls._create_provision_product(
            "Soporte", cls.categ_hardware, 'task_in_project', cls.pt_sub2,
            property_provision_implementation_id=cls.implementation_provision.id,
        )
        cls.equipment = cls._create_provision_product("Equipo", cls.categ_hardware, 'no', type='consu')
        cls.design = cls._create_provision_product("Diseño", cls.categ_hardware, 'project_only', cls.pt_std)
        cls.installation = cls._create_provision_product(
            "Instalación", cls.categ_hardware, 'task_global_project', project_id=cls.project_g.id,
        )
        cls.mouse = cls._create_provision_product("Mouse", cls.categ_peripherals, 'no', type='consu')
        cls.course = cls._create_provision_product("Curso", cls.categ_training, 'task_in_project', cls.pt_sub)

        # The tracking of the services is set before the accounts of the categories (R1.5).
        cls.categ_services.write({
            'property_provision_commission_categ_id': cls.commission_provision.id,
            'property_provision_implementation_categ_id': cls.implementation_provision.id,
        })
        cls.categ_licenses.property_provision_commission_categ_id = cls.commission_provision

    @classmethod
    def _create_provision_product(cls, name, category, tracking, template=None, **vals):
        return cls.env['product.template'].create({
            'name': name,
            'categ_id': category.id,
            'type': 'service',
            'service_tracking': tracking,
            'project_template_id': template.id if template else False,
            **vals,
        })

    def _invoice(self, lines, move_type='out_invoice', salesperson=None, date='2026-11-03', post=False, taxed=False, **vals):
        """ A customer invoice or credit note of company A with these lines: dicts with the product, the price
        (1 unit, no taxes), the analytic distribution and optionally the sale lines. """
        move = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.partner_a.id,
            'invoice_user_id': salesperson.id if salesperson else False,
            'invoice_date': date,
            'date': date,
            'invoice_line_ids': [Command.create({
                'product_id': line['product'].product_variant_id.id,
                'quantity': 1,
                'price_unit': line['price'],
                'tax_ids': [Command.set(self.tax_16.ids)] if taxed else [Command.clear()],
                'analytic_distribution': line.get('analytic') or False,
                'sale_line_ids': [Command.set(line['sale_lines'].ids)] if line.get('sale_lines') else [],
            }) for line in lines],
            **vals,
        })
        if post:
            move.action_post()
        return move

    def _pay(self, move, amount, date, currency=None):
        """ Register a payment of ``amount`` (all that is due, if empty) of a customer invoice or credit note,
        which reconciles it. """
        return self._register_payment(
            move, payment_date=date, **({'amount': amount} if amount else {}),
            **({'currency_id': currency.id} if currency else {}),
        )

    def _provision_entries(self):
        return self.env['account.move'].search([('provision_origin_move_id', '!=', False)], order='id')

    def _commission_bills(self):
        return self.env['account.move'].search([
            ('provision_partial_id', '!=', False), ('move_type', '=', 'in_invoice'),
        ], order='id')

    def _items(self, entry):
        """ The items of an entry as sorted (account code, debit, credit, partner, analytic ids). """
        return sorted(
            (
                item.account_id.code, item.debit, item.credit, item.partner_id.name or "",
                tuple(sorted(int(key) for key in (item.analytic_distribution or {}))),
            )
            for item in entry.line_ids
        )

    def _provisioned(self, line, role):
        """ What the provision entries provisioned of a line and a role, reversals included. """
        items = self.env['account.move.line'].search([
            ('provision_origin_line_id', '=', line.id), ('account_id.provision_type', '=', role),
            ('parent_state', '=', 'posted'),
        ])
        return -sum(items.mapped('balance'))

    def _balance(self, account, analytic):
        """ The credit balance of a provision account in an analytic account, from its posted items. """
        items = self.env['account.move.line'].search([
            ('account_id', '=', account.id), ('parent_state', '=', 'posted'),
            ('analytic_distribution', 'in', analytic.ids),
        ])
        return sum(-item.balance * item.analytic_distribution[str(analytic.id)] / 100.0 for item in items)


@tagged('post_install', '-at_install')
class ProvisionCase(ProvisionCommon):
    """ The invoice F1 of the spec (Consultoría in AA-X, Licencia in AA-Y and Equipo, of the salesperson Ana)
    and the helpers of the cases that start from it. """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.line_x = {'product': cls.consulting, 'price': 10000.0, 'analytic': {cls.analytic_x.id: 100}}
        cls.line_y = {'product': cls.license, 'price': 2000.0, 'analytic': {cls.analytic_y.id: 100}}
        cls.line_equipment = {'product': cls.equipment, 'price': 1000.0}
        cls.f1 = cls._invoice(cls, [cls.line_x, cls.line_y, cls.line_equipment], salesperson=cls.ana, post=True, taxed=True)
        cls.f1_x, cls.f1_y, _f1_equipment = cls.f1.invoice_line_ids

    def _e1(self):
        """ State E1: F1 posted and half paid by P1 (REP-1 and the draft bill FP-COM-1). """
        self._pay(self.f1, 7540.0, '2026-11-10')

    def _credit_note(self, lines, date='2026-11-12', origin=None, taxed=True):
        return self._invoice(
            lines, move_type='out_refund', salesperson=self.ana, date=date, post=True, taxed=taxed,
            reversed_entry_id=origin.id if origin else False,
        )

    def _bill(self, partner, lines, move_type='in_invoice', post=False):
        """ A vendor bill with lines (account, price) in AA-X or (account, price, analytic distribution). """
        bill = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': '2026-11-12',
            'invoice_line_ids': [Command.create({
                'name': "Line", 'account_id': account.id, 'quantity': 1, 'price_unit': price,
                'tax_ids': [Command.clear()],
                'analytic_distribution': (analytic[0] if analytic else {self.analytic_x.id: 100}) or False,
            }) for account, price, *analytic in lines],
        })
        if post:
            bill.action_post()
        return bill

    @staticmethod
    def _receivable(move):
        return move.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable')

    def _nc2(self):
        return self._credit_note([{**self.line_x, 'price': 1000.0}], date='2026-11-14')

    def _nc1(self):
        return self._credit_note([{**self.line_x, 'price': 2000.0}], origin=self.f1)

    def _f3(self):
        return self._invoice(
            [{'product': self.consulting, 'price': 5000.0, 'analytic': {self.analytic_x.id: 60, self.analytic_y.id: 40}}],
            salesperson=self.beto, date='2026-11-03', post=True, taxed=True,
        )
