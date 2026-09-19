# vykin-addons

Custom Odoo modules for Vykin. Branch `20.0` targets Odoo 20.0.

## Modules

- `vykin` (Base Vykin): installs the Vykin apps (CRM, Sales, Project, Surveys,
  Contacts, Calendar), the Mexican localization (`l10n_mx`) and sets the company
  data.

## Install

Create the database with Mexico as country (so the company currency is MXN)
and without demo data, then install the module:

```
odoo-bin db -c vykin200.conf init --country mx --password <admin-password> vykin200
odoo-bin module install -c vykin200.conf -d vykin200 vykin
```

Load `es_MX` only **after** installing the module:

```
odoo-bin -c vykin200.conf -d vykin200 --load-language es_MX --stop-after-init
```

With `es_MX` already active, loading the Mexican chart of accounts fails because
the translated journal code `MISCELÁNEO` does not fit `account_journal.code`
(varchar(7)).
