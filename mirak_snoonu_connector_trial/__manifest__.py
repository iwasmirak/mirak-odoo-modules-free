# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
{
    'name': 'Mirak Connector for Snoonu (Free Trial)',
    'version': '17.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'Free trial. Push prices, discounts, stock and availability from Odoo to the Snoonu marketplace, '
               'per branch, in bulk, automatically',
    'description': """
Mirak Connector for Snoonu
==========================
Keeps the Snoonu marketplace in line with Odoo using the Snoonu Merchant Integration API
(Inventory & Pricing Synchronization, PATCH /Product/{businessUnitId}/BulkUpdate).

* Several Snoonu accounts (brands / business units), staging and production.
* Branches linked to Odoo warehouses, with brand-level prices (UseDefaults) or branch prices.
* Prices from the Sales Price or any pricelist; pricelist reductions sent as Snoonu discount amounts.
* Stock per branch from the warehouse (available, on hand or forecasted) with a safety buffer.
* Hide products on Snoonu without touching stock (IsAvailable).
* Delta sync: only products whose price, discount, stock or availability changed are sent.
* Bulk batches, retries on temporary errors, payload preview, detailed logs.
""",
    'author': 'Bouchibane Karim',
    'maintainer': 'Bouchibane Karim',
    'license': 'OPL-1',
    'price': 0.0,
    'currency': 'USD',
    'support': 'iwasmirak@gmail.com',
    'depends': ['sale_stock', 'mail'],
    'data': [
        'security/snoonu_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/snoonu_account_views.xml',
        'views/snoonu_product_views.xml',
        'views/snoonu_log_views.xml',
        'views/product_views.xml',
        'wizard/snoonu_wizard_views.xml',
        'views/trial_views.xml',
        'views/menus.xml',
    ],
    'images': ['static/description/banner.png', 'static/description/icon.png', 'static/description/screenshot_dashboard.png', 'static/description/screenshot_account.png', 'static/description/screenshot_products.png', 'static/description/screenshot_logs.png'],
    'installable': True,
    'application': True,
}
