{
    'name': 'Project Subcontracting',
    'summary': 'Provision the payment of each sale and pay subcontracted implementers with vendor bills',
    'description': """
Project Subcontracting
======================
Splits what each customer payment of an implementation sale brings in between the salesperson,
the implementers and the company, and pays them with vendor bills, never more than what was
provisioned and never before the customer pays.

* Provision accounts are chosen on the product or its product category, like the expense
  account, and a product with one generates its project from a template with *Subcontracting*.
  The company sets the percentages (standard: 20% commission, 75% implementation; the company
  keeps the rest as income), the expense accounts and the journal.
* A posted customer invoice keeps the percentages and provision accounts of each line. Each
  reconciliation posts a provision entry for the share it covers, with the analytic account of
  the line, and creates a draft vendor bill for the commission of the salesperson.
  Unreconciling reverses them.
* A vendor bill against a provision cannot exceed its balance in the analytic account.
* Projects marked as *Subcontracting* run their tasks with subcontracted implementers.
  *Subcontracting* is an option of *Timesheets*: it only shows with them and is turned off
  with them.
* Each task of these projects has a subcontracting hour cost.
* The hours an assignee logs on a done task are *to bill*. *Bill Hours*, on the chosen
  timesheets, creates one draft vendor bill per collaborator, with a *Subcontracted Hours*
  line per task: the hours, the task's hour cost and the project's analytic account.
* Each timesheet is linked to its bill line and cannot be changed or deleted while the
  bill is not cancelled.
* The task warns about what departs from that flow: an hour cost of 0, hours logged by
  someone not assigned, hours to bill on a done task, and hours over the planned ones.
    """,
    'category': 'Services/Timesheets',
    'author': 'Vykin',
    'website': 'https://vykin.tech/',
    'version': '20.0.1.0.0',
    'license': 'LGPL-3',
    'depends': [
        'hr_timesheet',
        'account',
        'sale_project',
    ],
    'data': [
        'data/product_data.xml',
        'data/ir_actions_server.xml',
        'views/project_project_views.xml',
        'views/project_task_views.xml',
        'views/account_analytic_line_views.xml',
        'views/account_account_views.xml',
        'views/product_category_views.xml',
        'views/product_template_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'post_init_hook': '_setup_companies',
}
