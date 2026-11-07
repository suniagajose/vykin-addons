{
    'name': 'Sales Commissions on Installments',
    'summary': 'Fix sales commission achievements on invoices with installment payment terms',
    'description': """
Sales Commissions on Installments
=================================
The standard achievement report joins every product line of an invoice with
every payment term line to read the reconciliation date, so an invoice with N
installments counts each line N times (Amount Invoiced, Quantity Invoiced and
Amount Paid). This module keeps a single payment term line per invoice, the one
with the latest full reconciliation, so the achievement is counted once and its
date is the same as in standard.
    """,
    'category': 'Vykin',
    'author': 'Vykin',
    'website': 'https://vykin.tech/',
    'version': '20.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['sale_commission'],
}
