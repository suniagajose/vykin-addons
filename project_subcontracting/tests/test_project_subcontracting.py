from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestProjectSubcontracting(AccountTestInvoicingCommon):
    """ Acceptance cases CA-40 to CA-54 and CA-73 to CA-78 of the spec, on its data: project X
    with Timesheets and Subcontracting, Z with Timesheets only, W with neither; tasks T1 to T5
    and timesheets H1 to H8 of Carla, Diego and Eva. """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('project.group_project_manager')
            | cls.env.ref('hr_timesheet.group_timesheet_manager')
            | cls.env.ref('analytic.group_analytic_accounting')
        )
        cls.product = cls.env.ref('project_subcontracting.product_subcontracted_hours')
        project_plan, _other_plans = cls.env['account.analytic.plan']._get_all_plans()
        cls.analytic_x, cls.analytic_z, cls.analytic_w = cls.env['account.analytic.account'].create([
            {'name': name, 'plan_id': project_plan.id} for name in ("AA-X", "AA-Z", "AA-W")
        ])
        cls.project_x, cls.project_z, cls.project_w = cls.env['project.project'].create([
            {'name': "X", 'account_id': cls.analytic_x.id, 'allow_subcontracting': True},
            {'name': "Z", 'account_id': cls.analytic_z.id},
            {'name': "W", 'account_id': cls.analytic_w.id, 'allow_timesheets': False},
        ])
        cls.carla, cls.diego, cls.eva = (
            new_test_user(
                cls.env, login=login, name=name, groups='hr_timesheet.group_hr_timesheet_user', create_employee=True,
            )
            for login, name in (('carla', "Carla Ruiz"), ('diego', "Diego Gomez"), ('eva', "Eva"))
        )
        cls.t1, cls.t2, cls.t3, cls.t4, cls.t5 = cls.env['project.task'].create([
            {'name': name, 'project_id': project.id, 'subcontracting_hour_cost': cost, 'allocated_hours': planned,
             'user_ids': [Command.set(users.ids)]}
            for name, project, cost, planned, users in (
                ("T1 Migration", cls.project_x, 500.0, 10.0, cls.carla | cls.diego),
                ("T2 Reports", cls.project_x, 400.0, 0.0, cls.carla),
                ("T3 Support", cls.project_z, 0.0, 0.0, cls.carla),
                ("T4 Training", cls.project_x, 0.0, 0.0, cls.diego),
                ("T5 Integration", cls.project_x, 300.0, 0.0, cls.carla),
            )
        ])
        cls.h1 = cls._log(cls.t1, cls.carla, 3.0, '2026-11-02')
        cls.h2 = cls._log(cls.t1, cls.carla, 2.0, '2026-11-03')
        cls.h3 = cls._log(cls.t1, cls.diego, 4.0, '2026-11-04')
        cls.h4 = cls._log(cls.t1, cls.eva, 1.0, '2026-11-04')
        cls.h5 = cls._log(cls.t2, cls.carla, 2.0)
        cls.h6 = cls._log(cls.t3, cls.carla, 1.0)
        cls.h7 = cls._log(cls.t4, cls.diego, 1.0)
        cls.h8 = cls._log(cls.t5, cls.carla, 2.0)
        (cls.t1 | cls.t3 | cls.t4 | cls.t5).state = '1_done'

    @classmethod
    def _log(cls, task, user, hours, date=None):
        return cls.env['account.analytic.line'].create({
            'name': "/", 'task_id': task.id, 'project_id': task.project_id.id,
            'employee_id': user.employee_id.id, 'unit_amount': hours,
            **({'date': date} if date else {}),
        })

    def _bills(self):
        return self.env['account.move'].search([
            ('move_type', '=', 'in_invoice'),
            ('partner_id', 'in', (self.carla | self.diego | self.eva).partner_id.ids),
        ])

    def _provision(self, amount):
        """ Provision ``amount`` of implementation in X, as a customer payment would (a bill needs it, R6). """
        company = self.env.company
        provision = self.env['account.account'].search([
            ('provision_type', '=', 'implementation'), ('company_ids', 'in', company.id),
        ], limit=1)
        distribution = {self.analytic_x.id: 100}
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': company.provision_journal_id.id, 'date': fields.Date.today(),
            'line_ids': [
                Command.create({'account_id': company.provision_implementation_expense_account_id.id, 'debit': amount, 'analytic_distribution': distribution}),
                Command.create({'account_id': provision.id, 'credit': amount, 'analytic_distribution': distribution}),
            ],
        }).action_post()

    def _bill_hours(self, timesheets):
        timesheets.action_bill_subcontracted_hours()
        return timesheets.subcontracting_bill_line_id.move_id

    def _warning(self, task):
        return task.subcontracting_warning or ""

    def test_ca40_task_not_done_or_project_without_subcontracting(self):
        """ CA-40 (R5.1): no bill for H5 (T2 in progress) nor for H6 (T3, Z without Subcontracting). """
        with self.assertRaisesRegex(UserError, "its task is not done"):
            self.h5.action_bill_subcontracted_hours()
        with self.assertRaisesRegex(UserError, "does not have Subcontracting"):
            self.h6.action_bill_subcontracted_hours()
        self.assertFalse(self._bills())

    def test_ca41_task_done(self):
        """ CA-41 (R5.1): once T2 is done, H5 is billed to Carla Ruiz, 2 h x 400.00 = 800.00. """
        self.t2.state = '1_done'
        bill = self._bill_hours(self.h5)
        self.assertRecordValues(bill, [{
            'move_type': 'in_invoice', 'state': 'draft', 'partner_id': self.carla.partner_id.id,
            'amount_untaxed': 800.0,
        }])
        self.assertRecordValues(bill.invoice_line_ids, [{'quantity': 2.0, 'price_unit': 400.0}])

    def test_ca42_hour_cost(self):
        """ CA-42 (R5.1.1): T1 at 500.00 without warning, T4 at 0.00 with a warning, T3 without
        the field. """
        self.assertNotIn("hour cost is 0", self._warning(self.t1))
        self.assertIn("hour cost is 0", self._warning(self.t4))
        self.assertTrue(self.t1.allow_subcontracting)
        self.assertFalse(self.t3.allow_subcontracting)

    def test_ca43_unassigned_hours(self):
        """ CA-43 (R5.7.1): T1 warns that the hour of Eva cannot be billed; T5 does not. """
        self.assertIn("Eva logged 1.00 hours without being assigned", self._warning(self.t1))
        self.assertNotIn("without being assigned", self._warning(self.t5))

    def test_ca44_hours_to_bill(self):
        """ CA-44 (R5.7.2): T1 warns about the hours to bill until they are billed; T2, in
        progress, does not. """
        self.assertIn("Carla Ruiz has 5.00 hours to bill", self._warning(self.t1))
        self.assertIn("Diego Gomez has 4.00 hours to bill", self._warning(self.t1))
        self.assertNotIn("hours to bill", self._warning(self.t2))
        self._bill_hours(self.h1 | self.h2 | self.h3)
        self.assertNotIn("hours to bill", self._warning(self.t1))

    def test_ca45_over_planned(self):
        """ CA-45 (R5.7.3): with H9, 12 h logged on T1 against 10 planned: a warning that does
        not block; Diego is billed 6 h x 500.00 = 3,000.00. """
        h9 = self._log(self.t1, self.diego, 2.0)
        self.assertIn("12.00 hours were logged and 10.00 were planned", self._warning(self.t1))
        bill = self._bill_hours(self.h3 | h9)
        self.assertRecordValues(bill, [{'partner_id': self.diego.partner_id.id, 'amount_untaxed': 3000.0}])

    def test_ca46_not_over_planned(self):
        """ CA-46 (R5.7.3): no warning on T1 with as many hours logged as planned, nor on T5
        without planned hours. """
        self.assertEqual(self.t1.effective_hours, 10.0)
        self.assertNotIn("were planned", self._warning(self.t1))
        self.assertNotIn("were planned", self._warning(self.t5))

    def test_ca47_project_without_subcontracting(self):
        """ CA-47 (R5.7): T3, of Z, shows no subcontracting warning, even with hours of Eva. """
        self._log(self.t3, self.eva, 1.0)
        self.assertFalse(self.t3.subcontracting_warning)

    def test_ca48_to_bill(self):
        """ CA-48 (R5.2): To Bill shows H1, H2, H3, H7 and H8, and Carla's own hours among them
        under My Subcontracted Hours; H1 leaves it once billed. """
        Timesheet = self.env['account.analytic.line']
        timesheets = self.h1 | self.h2 | self.h3 | self.h4 | self.h5 | self.h6 | self.h7 | self.h8
        domain = [('subcontracting_billable', '=', True), ('id', 'in', timesheets.ids)]
        self.assertEqual(Timesheet.search(domain), self.h1 | self.h2 | self.h3 | self.h7 | self.h8)
        my_action = self.env.ref('project_subcontracting.action_my_subcontracting_billable_timesheets')
        my_domain = safe_eval(my_action.domain, {'uid': self.carla.id}) + [('id', 'in', timesheets.ids)]
        self.assertEqual(Timesheet.with_user(self.carla).search(my_domain), self.h1 | self.h2 | self.h8)
        self._bill_hours(self.h1)
        self.assertEqual(Timesheet.search(domain), self.h2 | self.h3 | self.h7 | self.h8)

    def test_ca49_bill_hours(self):
        """ CA-49 (R5.3): one draft bill per collaborator, a line per task in the order of the
        tasks, on AA-X, each timesheet linked to its line. """
        self._bill_hours(self.h1 | self.h2 | self.h3 | self.h8)
        bills = self._bills()
        bill_carla = bills.filtered(lambda bill: bill.partner_id == self.carla.partner_id)
        bill_diego = bills.filtered(lambda bill: bill.partner_id == self.diego.partner_id)
        self.assertEqual(len(bills), 2)
        lines_carla = bill_carla.invoice_line_ids.sorted('id')
        self.assertRecordValues(lines_carla | bill_diego.invoice_line_ids, [
            {'quantity': 5.0, 'price_unit': 500.0},
            {'quantity': 2.0, 'price_unit': 300.0},
            {'quantity': 4.0, 'price_unit': 500.0},
        ])
        self.assertRecordValues(bills.invoice_line_ids, [
            {'product_id': self.product.id, 'analytic_distribution': {str(self.analytic_x.id): 100.0}},
        ] * 3)
        self.assertRecordValues(bills, [{'state': 'draft'}] * 2)
        self.assertEqual((self.h1 | self.h2).subcontracting_bill_line_id, lines_carla[0])
        self.assertEqual(self.h8.subcontracting_bill_line_id, lines_carla[1])
        self.assertEqual(self.h3.subcontracting_bill_line_id, bill_diego.invoice_line_ids)

    def test_ca49_bill_posted_from_timesheets(self):
        """ CA-49 (R5.3, R8.1): a bill opened from the timesheets and posted there charges its
        analytic account with its amounts, not as timesheets valued at the employee's cost. """
        # The billing user logs time on X, so X is the favorite project of their new timesheets.
        employee = self.env['hr.employee'].create({'name': "Biller", 'user_id': self.env.user.id})
        self.env['account.analytic.line'].create([
            {'name': "/", 'task_id': self.t2.id, 'project_id': self.project_x.id, 'employee_id': employee.id, 'unit_amount': 1.0}
        ] * 3)
        self._provision(10000.0)
        timesheets = (self.h1 | self.h2).with_context(is_timesheet=1, search_default_groupby_employee=1)
        action = timesheets.action_bill_subcontracted_hours()
        bill = self.env['account.move'].with_context(action['context']).browse(action['res_id'])
        bill.invoice_date = fields.Date.today()
        bill.action_post()
        self.assertRecordValues(bill.invoice_line_ids.analytic_line_ids, [
            {'amount': -2500.0, 'project_id': False, 'account_id': self.analytic_x.id},
        ])

    def test_ca50_release(self):
        """ CA-50 (R5.4): cancelling Carla's bill and deleting Diego's leaves H1, H2, H8 and H3
        to bill again. """
        timesheets = self.h1 | self.h2 | self.h3 | self.h8
        self._provision(10000.0)
        self._bill_hours(timesheets)
        bill_carla = self.h1.subcontracting_bill_id
        bill_carla.invoice_date = fields.Date.today()
        bill_carla.action_post()
        bill_carla.button_draft()
        bill_carla.button_cancel()
        self.h3.subcontracting_bill_id.unlink()
        self.assertEqual(timesheets.filtered('subcontracting_billable'), timesheets)

    def test_ca51_billed_locked(self):
        """ CA-51 (R5.5): H1, billed, cannot change its hours, date, task or user, nor be
        deleted; each attempt names the bill. Its description can change. """
        bill = self._bill_hours(self.h1)
        for vals in (
            {'unit_amount': 4.0}, {'date': '2026-11-05'}, {'task_id': self.t5.id},
            {'employee_id': self.diego.employee_id.id},
        ):
            with self.subTest(vals=vals), self.assertRaisesRegex(UserError, bill.display_name):
                self.h1.write(vals)
        with self.assertRaisesRegex(UserError, f"{bill.display_name} - Carla Ruiz"):
            self.h1.unlink()
        self.assertTrue(self.h1.readonly_timesheet)
        self.h1.name = "Migration session"

    def test_ca52_cancelled_bill_unlocks(self):
        """ CA-52 (R5.5): once its bill is cancelled, H1 can change to 4 h. """
        self._bill_hours(self.h1).button_cancel()
        self.h1.unit_amount = 4.0

    def test_ca53_unassigned_rejected(self):
        """ CA-53 (R5.6): H1 with H4 creates no bill, not even Carla's, and points at Eva. """
        with self.assertRaisesRegex(UserError, "Eva is not assigned"):
            (self.h1 | self.h4).action_bill_subcontracted_hours()
        self.assertFalse(self._bills())

    def test_ca54_not_billable(self):
        """ CA-54 (R5.6): H3 with H7 (T4 at 0), H1 already billed, and H5 (T2 not done) are each
        rejected without a bill. """
        with self.assertRaisesRegex(UserError, "no subcontracting hour cost"):
            (self.h3 | self.h7).action_bill_subcontracted_hours()
        bill = self._bill_hours(self.h1)
        with self.assertRaisesRegex(UserError, "already billed"):
            self.h1.action_bill_subcontracted_hours()
        with self.assertRaisesRegex(UserError, "its task is not done"):
            self.h5.action_bill_subcontracted_hours()
        self.assertEqual(self._bills(), bill)

    def test_ca73_ca75_option_of_timesheets(self):
        """ CA-73, CA-75 (R5.8): Subcontracting does not show on W without Timesheets, and shows
        and can be set once they are on. """
        with Form(self.project_w, view='project.edit_project') as project_form:
            self.assertTrue(project_form._get_modifier('allow_subcontracting', 'invisible'))
            project_form.allow_timesheets = True
            self.assertFalse(project_form._get_modifier('allow_subcontracting', 'invisible'))
            project_form.allow_subcontracting = True
        self.assertTrue(self.project_w.allow_subcontracting)

    def test_ca74_not_without_timesheets(self):
        """ CA-74 (R5.8): neither a mass edit nor an import sets Subcontracting without Timesheets. """
        with self.assertRaisesRegex(ValidationError, "needs the timesheets"):
            self.project_w.write({'allow_subcontracting': True})
        result = self.env['project.project'].load(
            ['name', 'allow_timesheets', 'allow_subcontracting'], [["W2", "False", "True"]],
        )
        self.assertFalse(result['ids'])
        self.assertFalse(self.project_w.allow_subcontracting)

    def test_ca76_timesheets_turned_off(self):
        """ CA-76 (R5.8.1): turning off the Timesheets of X turns off its Subcontracting and
        keeps the hour cost of T1; turning them on does not set it again. """
        self.project_x.allow_timesheets = False
        self.assertFalse(self.project_x.allow_subcontracting)
        self.assertFalse(self.t1.allow_subcontracting)
        self.assertFalse(self.t1.subcontracting_warning)
        with self.assertRaisesRegex(UserError, "does not have Subcontracting"):
            (self.h1 | self.h2 | self.h3).action_bill_subcontracted_hours()
        self.project_x.allow_timesheets = True
        self.assertFalse(self.project_x.allow_subcontracting)
        self.project_x.allow_subcontracting = True
        self.assertRecordValues(self.t1, [{'allow_subcontracting': True, 'subcontracting_hour_cost': 500.0}])

    def test_ca77_without_subcontracting(self):
        """ CA-77 (R5.8.1): turning off the Timesheets of Z leaves Subcontracting off. """
        self.project_z.allow_timesheets = False
        self.assertFalse(self.project_z.allow_subcontracting)

    def test_ca78_billed_kept(self):
        """ CA-78 (R5.8.1): turning off the Timesheets of X keeps H1, H2 and H8 linked to their
        draft bill, and locked. """
        self._bill_hours(self.h1 | self.h2 | self.h3 | self.h8)
        bill_carla = self.h1.subcontracting_bill_id
        self.project_x.allow_timesheets = False
        self.assertEqual((self.h1 | self.h2 | self.h8).subcontracting_bill_id, bill_carla)
        with self.assertRaisesRegex(UserError, bill_carla.display_name):
            self.h1.unit_amount = 4.0
