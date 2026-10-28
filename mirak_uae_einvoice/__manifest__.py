# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
{
    'name': 'Mirak UAE E-Invoicing (PINT-AE) — Free Trial',
    'version': '19.0.1.0.2',
    'category': 'Accounting/Localizations/EDI',
    'summary': 'FREE TRIAL: UAE e-invoicing for Odoo Community: PINT-AE UBL 2.1 invoices and credit notes, '
               'pre-validation, readiness checker, vendor bill import. Simulation provider, 15 documents, '
               'QR code to buy the full version.',
    'description': 'UAE electronic invoicing (Peppol PINT-AE 1.0.4): see README.rst and static/description.',
    'author': 'Bouchibane Karim (Mirak)',
    'maintainer': 'Bouchibane Karim (Mirak)',
    'support': 'iwasmirak@gmail.com',
    'images': ['static/description/banner.png', 'static/description/icon.png', 'static/description/screenshot_dashboard.png', 'static/description/screenshot_invoice.png', 'static/description/screenshot_provider.png', 'static/description/screenshot_readiness.png'],
    'license': 'OPL-1',
    'price': 0.0,
    'currency': 'USD',
    'depends': ['account'],
    'external_dependencies': {'python': ['lxml', 'requests']},
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/provider_data.xml',
        'data/cron.xml',
        'wizard/send_wizard_views.xml',
        'wizard/import_wizard_views.xml',
        'views/provider_views.xml',
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/account_tax_views.xml',
        'views/product_views.xml',
        'views/account_move_views.xml',
        'wizard/readiness_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mirak_uae_einvoice/static/src/scss/mirak_ae.scss',
        ],
    },
    'installable': True,
    'application': True,
}
