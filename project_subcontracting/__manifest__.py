{
    'name': 'Project Subcontracting',
    'summary': 'Bill the hours logged by subcontracted implementers on vendor bills',
    'description': """
Project Subcontracting
======================
Projects marked as *Subcontracting* run their tasks with subcontracted implementers.
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
    ],
    'data': [
        'data/product_data.xml',
        'data/ir_actions_server.xml',
        'views/project_project_views.xml',
        'views/project_task_views.xml',
        'views/account_analytic_line_views.xml',
    ],
}
