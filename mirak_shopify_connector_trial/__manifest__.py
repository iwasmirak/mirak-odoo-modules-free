# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/18.0/legal/licenses.html)
{
    'name': 'Mirak Connector for Shopify (Free Trial)',
    'version': '18.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'Free trial (25 products): two-way Shopify sync: products, images, tags, collections, multi-location stock, orders, '
               'customers, invoices, payouts, returns, real-time webhooks, several stores',
    'description': """
Mirak Connector for Shopify
===========================
Two-way synchronization between Odoo and one or several Shopify stores:

* Products and variants, prices and compare-at prices, Shopify Markets prices, SKU / barcodes,
  descriptions, status, image galleries, tags, product types, collections, metafields, translations.
* Stock of several Shopify locations kept in line with Odoo warehouses, in both directions.
* Shopify orders imported as confirmed sales orders with customers, addresses, shipping, discounts and
  taxes; order edits, POS orders, cancellations both ways.
* Customers both ways, abandoned checkouts as CRM opportunities.
* Invoices, payments per payment method, refunds as credit notes, returns both ways,
  Shopify Payments payouts with fees and reconciliation.
* Deliveries validated in Odoo are fulfilled in Shopify (or the opposite).
* Real-time updates (webhooks), install link (OAuth), dashboard, detailed logs.
""",
    'author': 'Bouchibane Karim',
    'maintainer': 'Bouchibane Karim',
    'license': 'OPL-1',
    'support': 'iwasmirak@gmail.com',
    'price': 0.0,
    'currency': 'USD',
    'images': ['static/description/banner.png', 'static/description/icon.png', 'static/description/screenshot_dashboard.png', 'static/description/screenshot_store.png', 'static/description/screenshot_product.png', 'static/description/screenshot_links.png', 'static/description/screenshot_logs.png'],
    'depends': ['sale_stock'],
    'external_dependencies': {'python': ['cryptography']},
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/mirak_shopify_store_views.xml',
        'views/mirak_shopify_product_map_views.xml',
        'views/mirak_shopify_log_views.xml',
        'views/product_template_views.xml',
        'views/sale_views.xml',
        'views/dashboard_views.xml',
        'views/menus.xml',
        'views/mirak_shopify_extra_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mirak_shopify_connector_trial/static/src/dashboard/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}
