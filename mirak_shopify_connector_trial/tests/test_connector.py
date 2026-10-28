# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""End-to-end tests of the connector with a fake Shopify Admin API.

The fake client records every GraphQL call and answers with realistic payloads, so the
whole synchronization logic (import, export, stock, orders, webhooks) runs without a
Shopify store, a network connection or a paid plan.
"""
import base64
import hashlib
import hmac
import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..models import mirak_license
from ..models.mirak_shopify_store import MirakShopifyStore


class FakeShopifyClient:
    """Minimal stand-in for ``ShopifyClient``: records calls, returns plausible payloads."""

    def __init__(self, currency='USD'):
        self.calls = []
        self.currency = currency
        self.levels = {}          # inventory item gid -> available quantity
        self.media = []           # media nodes returned by MEDIA_QUERY
        self._counter = 500

    # -- helpers -------------------------------------------------------
    def next_gid(self, kind):
        self._counter += 1
        return f"gid://shopify/{kind}/{self._counter}"

    def _product_node(self, product_gid, data, variants):
        return {
            'id': product_gid,
            'title': data.get('title', 'Product'),
            'descriptionHtml': data.get('descriptionHtml', ''),
            'status': data.get('status', 'ACTIVE'),
            'updatedAt': '2026-01-01T10:00:00Z',
            'tags': data.get('tags', []),
            'productType': data.get('productType', ''),
            'options': [
                {'name': option['name'], 'position': option['position'],
                 'values': [value['name'] for value in option['values']]}
                for option in data.get('productOptions', [])
            ],
            'variants': {'pageInfo': {'hasNextPage': False}, 'nodes': variants},
        }

    # -- API -----------------------------------------------------------
    def query(self, query, variables=None):
        variables = variables or {}
        self.calls.append(('query', query, variables))
        if 'shop {' in query:
            return {'shop': {'name': 'Test Shop', 'myshopifyDomain': 'test.myshopify.com',
                             'currencyCode': self.currency}}
        if 'locations(' in query:
            return {'locations': {'nodes': [{'id': 'gid://shopify/Location/1', 'name': 'Main',
                                             'isActive': True}]}}
        if 'media(first' in query:
            return {'product': {'id': variables.get('id'), 'media': {'nodes': list(self.media)},
                                'variants': {'nodes': []}}}
        if 'nodes(ids:' in query:
            return {'nodes': [{
                'id': gid,
                'tracked': True,
                'inventoryLevel': {'quantities': [{'name': 'available',
                                                   'quantity': self.levels.get(gid, 0)}]},
            } for gid in variables.get('ids', [])]}
        if 'webhookSubscriptions' in query:
            return {'webhookSubscriptions': {'nodes': []}}
        return {}

    def mutate(self, query, variables, key):
        self.calls.append(('mutate', key, variables))
        if key == 'productSet':
            data = variables['input']
            product_gid = (variables.get('identifier') or {}).get('id') or self.next_gid('Product')
            variants = []
            for variant in data.get('variants', []):
                variants.append({
                    'id': variant.get('id') or self.next_gid('ProductVariant'),
                    'title': ' / '.join(option['name'] for option in variant.get('optionValues', [])),
                    'sku': variant.get('sku'),
                    'barcode': variant.get('barcode'),
                    'price': variant.get('price', '0.00'),
                    'selectedOptions': [{'name': option['optionName'], 'value': option['name']}
                                        for option in variant.get('optionValues', [])],
                    'inventoryItem': {'id': variant.get('inventoryItem', {}).get('id')
                                      or self.next_gid('InventoryItem'), 'tracked': True},
                })
            return {'product': self._product_node(product_gid, data, variants)}
        if key == 'productUpdate':
            data = variables.get('product', {})
            return {'product': {'id': data.get('id') or self.next_gid('Product'),
                                'updatedAt': '2026-01-01T11:00:00Z',
                                'status': data.get('status', 'ACTIVE'),
                                'media': {'nodes': list(self.media)}}}
        if key == 'productVariantsBulkUpdate':
            return {'product': {'id': variables.get('productId'), 'updatedAt': '2026-01-01T11:00:00Z'}}
        if key == 'inventorySetQuantities':
            return {'userErrors': []}
        if key == 'inventoryActivate':
            return {'inventoryLevel': {'id': self.next_gid('InventoryLevel')}, 'userErrors': []}
        if key == 'collectionCreate':
            return {'collection': {'id': self.next_gid('Collection'), 'title': 'Collection'}}
        if key == 'customerSet':
            return {'customer': {'id': self.next_gid('Customer'), 'updatedAt': '2026-01-01T10:00:00Z'}}
        if key == 'webhookSubscriptionCreate':
            return {'webhookSubscription': {'id': self.next_gid('WebhookSubscription'),
                                            'topic': variables.get('topic'), 'uri': 'x'}}
        if key == 'fulfillmentCreate':
            return {'fulfillment': {'id': self.next_gid('Fulfillment'), 'status': 'SUCCESS'}}
        if key == 'orderCancel':
            return {'job': {'id': self.next_gid('Job')}, 'orderCancelUserErrors': []}
        return {}

    def paginate(self, query, variables, connection):
        self.calls.append(('paginate', connection, variables))
        return iter(())


@tagged('post_install', '-at_install')
class MirakConnectorCase(TransactionCase):
    """Common fixtures: a connected store on the main warehouse, a tax and two products."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._license_required = mirak_license.LICENSE_KEY_REQUIRED
        mirak_license.LICENSE_KEY_REQUIRED = False
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.tax = cls.env['account.tax'].create({
            'name': 'VAT 15%', 'amount': 15.0, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })
        cls.store = cls.env['mirak.shopify.store'].create({
            'name': 'Test Store',
            'shop_domain': 'test.myshopify.com',
            'auth_method': 'token',
            'access_token': 'shpat_test',
            'warehouse_id': cls.warehouse.id,
            'sync_images': False,
            'order_tax_mode': 'none',
        })
        cls.location = cls.env['mirak.shopify.location'].create({
            'store_id': cls.store.id, 'name': 'Main', 'gid': 'gid://shopify/Location/1',
            'is_main': True,
        })
        cls.store.location_id = cls.location
        cls.store.state = 'connected'   # only connected stores receive the change queue
        cls.client = FakeShopifyClient()
        # every call to the Shopify API goes to the fake client
        cls._client_patch = patch.object(MirakShopifyStore, '_client', lambda store: cls.client)
        cls._client_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._client_patch.stop()
        mirak_license.LICENSE_KEY_REQUIRED = cls._license_required
        super().tearDownClass()

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _tax_field(line):
        """sale.order.line uses tax_id before Odoo 19 and tax_ids from Odoo 19."""
        return 'tax_id' if 'tax_id' in line._fields else 'tax_ids'

    def _product(self, name, price, sku=None, storable=True):
        vals = {'name': name, 'list_price': price, 'default_code': sku, 'ms_sync': True}
        # Odoo 19 dropped the 'All' product category
        category = self.env.ref('product.product_category_all', raise_if_not_found=False)
        if category:
            vals['categ_id'] = category.id
        template_fields = self.env['product.template']._fields
        if 'is_storable' in template_fields:
            vals.update(type='consu', is_storable=storable)
        else:
            vals.update(type='product' if storable else 'consu')
        return self.env['product.template'].create(vals)

    def _shopify_product(self, title='Shopify Hoodie', price='59.90', sku='HOOD-1'):
        product_gid = 'gid://shopify/Product/777'
        return {
            'id': product_gid,
            'title': title,
            'descriptionHtml': '<p>A warm hoodie</p>',
            'status': 'ACTIVE',
            'updatedAt': '2026-01-01T10:00:00Z',
            'tags': ['winter', 'men'],
            'productType': 'Apparel',
            'options': [{'name': 'Title', 'position': 1, 'values': ['Default Title']}],
            'variants': {'pageInfo': {'hasNextPage': False}, 'nodes': [{
                'id': 'gid://shopify/ProductVariant/888',
                'title': 'Default Title',
                'sku': sku,
                'barcode': '1234567890123',
                'price': price,
                'selectedOptions': [{'name': 'Title', 'value': 'Default Title'}],
                'inventoryItem': {'id': 'gid://shopify/InventoryItem/999', 'tracked': True},
            }]},
        }

    def _order_node(self, name='#1001', qty=2, price='59.90', currency='USD'):
        return {
            'id': f'gid://shopify/Order/{name.strip("#")}',
            'name': name,
            'createdAt': '2026-01-02T09:00:00Z',
            'updatedAt': '2026-01-02T09:30:00Z',
            'cancelledAt': None,
            'displayFinancialStatus': 'PAID',
            'displayFulfillmentStatus': 'UNFULFILLED',
            'currencyCode': currency,
            'taxesIncluded': False,
            'sourceName': 'web',
            'retailLocation': None,
            'email': 'jane@example.com',
            'phone': '+33123456789',
            'note': 'Leave at the door',
            'paymentGatewayNames': ['shopify_payments'],
            'currentTotalPriceSet': {'shopMoney': {'amount': '119.80'}},
            'totalReceivedSet': {'shopMoney': {'amount': '119.80'}},
            'totalRefundedSet': {'shopMoney': {'amount': '0.00'}},
            'customer': {'id': 'gid://shopify/Customer/42', 'firstName': 'Jane', 'lastName': 'Doe',
                         'email': 'jane@example.com', 'phone': '+33123456789'},
            'billingAddress': {'name': 'Jane Doe', 'address1': '1 Main Street', 'address2': '',
                               'city': 'Paris', 'zip': '75001', 'provinceCode': None,
                               'countryCodeV2': 'FR', 'phone': '+33123456789', 'company': ''},
            'shippingAddress': {'name': 'Jane Doe', 'address1': '1 Main Street', 'address2': '',
                                'city': 'Paris', 'zip': '75001', 'provinceCode': None,
                                'countryCodeV2': 'FR', 'phone': '+33123456789', 'company': ''},
            'shippingLines': {'nodes': [{'title': 'Standard', 'taxLines': [],
                                         'discountedPriceSet': {'shopMoney': {'amount': '0.00'}}}]},
            'lineItems': {'pageInfo': {'hasNextPage': False}, 'nodes': [{
                'id': 'gid://shopify/LineItem/555',
                'name': 'Shopify Hoodie',
                'sku': 'HOOD-1',
                'currentQuantity': qty,
                'variant': {'id': 'gid://shopify/ProductVariant/888',
                            'product': {'id': 'gid://shopify/Product/777'}},
                'discountedUnitPriceAfterAllDiscountsSet': {'shopMoney': {'amount': price}},
                'taxLines': [],
            }]},
        }

    def _last_call(self, key):
        for kind, name, variables in reversed(self.client.calls):
            if name == key:
                return variables
        return None


class TestStoreAndLicence(MirakConnectorCase):

    def test_store_defaults(self):
        # the shared store is already connected: check the defaults on an unsaved record
        store = self.env['mirak.shopify.store'].new({
            'name': 'Defaults', 'shop_domain': 'defaults.myshopify.com'})
        self.assertTrue(store.auto_sync)
        self.assertEqual(store.sync_interval, 5)
        self.assertEqual(store.state, 'draft')
        self.assertEqual(store.export_policy, 'all')
        self.assertEqual(store.stock_qty_type, 'free')

    def test_connection_marks_store_connected(self):
        self.store.action_test_connection()
        self.assertEqual(self.store.state, 'connected')
        self.assertEqual(self.store.shop_currency_id.name, 'USD')
        self.assertTrue(self.store.location_ids)

    def test_stock_rules(self):
        self.store.write({'stock_percent': 50.0, 'stock_buffer': 2})
        self.assertEqual(self.store._apply_stock_rules(20), 8)   # 20 * 50% - 2
        self.assertEqual(self.store._apply_stock_rules(3), 0)    # never negative
        self.store.write({'stock_percent': 100.0, 'stock_buffer': 0})
        self.assertEqual(self.store._apply_stock_rules(7), 7)


class TestProductImport(MirakConnectorCase):

    def test_import_creates_product_and_link(self):
        node = self._shopify_product()
        self.assertTrue(self.store._import_product(node))
        product = self.env['product.product'].search([('default_code', '=', 'HOOD-1')], limit=1)
        self.assertTrue(product, "the Shopify variant must become an Odoo product")
        self.assertEqual(product.name, 'Shopify Hoodie')
        self.assertAlmostEqual(product.lst_price, 59.90)
        self.assertEqual(product.barcode, '1234567890123')
        self.assertIn('A warm hoodie', product.description_sale)
        link = self.env['mirak.shopify.product.map'].search([('store_id', '=', self.store.id)])
        self.assertEqual(len(link), 1)
        self.assertEqual(link.shopify_variant_gid, 'gid://shopify/ProductVariant/888')
        self.assertEqual(link.inventory_item_gid, 'gid://shopify/InventoryItem/999')
        self.assertEqual(link.origin, 'shopify')
        self.assertTrue(product.product_tmpl_id.ms_sync)

    def test_import_twice_is_idempotent(self):
        node = self._shopify_product()
        self.store._import_product(node)
        self.assertFalse(self.store._import_product(node), "an unchanged product must be skipped")
        self.assertEqual(self.env['mirak.shopify.product.map'].search_count(
            [('store_id', '=', self.store.id)]), 1)

    def test_import_matches_existing_sku(self):
        existing = self._product('Existing hoodie', 10.0, sku='HOOD-1')
        self.store._import_product(self._shopify_product())
        self.assertEqual(self.env['product.product'].search_count([('default_code', '=', 'HOOD-1')]), 1,
                         "the variant must be linked to the product with the same SKU")
        self.assertTrue(existing.ms_sync)

    def test_import_multi_variant_creates_attributes(self):
        node = self._shopify_product()
        node['options'] = [{'name': 'Size', 'position': 1, 'values': ['S', 'M']}]
        node['variants'] = {'pageInfo': {'hasNextPage': False}, 'nodes': [
            {'id': 'gid://shopify/ProductVariant/1', 'title': 'S', 'sku': 'HOOD-S', 'barcode': False,
             'price': '59.90', 'selectedOptions': [{'name': 'Size', 'value': 'S'}],
             'inventoryItem': {'id': 'gid://shopify/InventoryItem/1', 'tracked': True}},
            {'id': 'gid://shopify/ProductVariant/2', 'title': 'M', 'sku': 'HOOD-M', 'barcode': False,
             'price': '64.90', 'selectedOptions': [{'name': 'Size', 'value': 'M'}],
             'inventoryItem': {'id': 'gid://shopify/InventoryItem/2', 'tracked': True}},
        ]}
        self.store._import_product(node)
        template = self.env['product.template'].search([('name', '=', 'Shopify Hoodie')], limit=1)
        self.assertEqual(template.product_variant_count, 2)
        self.assertEqual(template.attribute_line_ids.attribute_id.name, 'Size')
        # the price difference is kept as a price extra
        self.assertAlmostEqual(
            template.attribute_line_ids.product_template_value_ids.filtered(
                lambda v: v.name == 'M').price_extra, 5.0)


class TestProductExport(MirakConnectorCase):

    def test_new_product_is_sent_complete(self):
        template = self._product('Odoo Mug', 12.5, sku='MUG-1')
        template._ms_mark_dirty({'all'})
        self.store._push_products(template)
        payload = self._last_call('productSet')
        self.assertIsNotNone(payload, "a productSet mutation must be sent")
        self.assertEqual(payload['input']['title'], 'Odoo Mug')
        self.assertEqual(payload['input']['variants'][0]['sku'], 'MUG-1')
        self.assertEqual(payload['input']['variants'][0]['price'], '12.50')
        self.assertTrue(self.env['mirak.shopify.product.map'].search_count(
            [('store_id', '=', self.store.id), ('product_tmpl_id', '=', template.id)]))
        self.assertEqual(template.ms_shopify_status, 'active')

    def test_pending_changes_are_queued_per_store(self):
        template = self._product('Odoo Lamp', 40.0, sku='LAMP-1')
        self.assertTrue(template.ms_pending_export)
        pending = self.env['mirak.shopify.pending'].search([('product_tmpl_id', '=', template.id)])
        self.assertIn('all', pending.tag_set())
        template.name = 'Odoo Lamp XL'
        self.assertIn('name', self.store._pending_tags(template))

    def test_price_from_pricelist(self):
        template = self._product('Pricelist item', 100.0, sku='PL-1')
        pricelist = self.env['product.pricelist'].create({'name': 'Shop', 'currency_id': self.currency.id})
        self.env['product.pricelist.item'].create({
            'pricelist_id': pricelist.id, 'applied_on': '0_product_variant',
            'product_id': template.product_variant_id.id, 'compute_price': 'fixed', 'fixed_price': 80.0,
        })
        self.store.write({'price_source': 'pricelist', 'pricelist_id': pricelist.id})
        self.assertAlmostEqual(self.store._variant_price(template.product_variant_id), 80.0)

    def test_shopify_price_is_applied_to_odoo(self):
        template = self._product('Discounted', 100.0, sku='DISC-1')
        product = template.product_variant_id
        self.store._set_odoo_variant_price(product, 75.0)
        self.assertAlmostEqual(template.list_price, 75.0)


class TestOrders(MirakConnectorCase):

    def setUp(self):
        super().setUp()
        self.store.import_orders = True
        self.store.write({'order_import_from': '2020-01-01 00:00:00'})
        self.store._import_product(self._shopify_product())

    def test_order_imported_as_sale_order(self):
        self.store._process_order(self._order_node())
        order = self.env['sale.order'].search([('ms_order_gid', '!=', False)], limit=1)
        self.assertTrue(order, "the Shopify order must become a sale order")
        self.assertEqual(order.client_order_ref, '#1001')
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.partner_id.email, 'jane@example.com')
        self.assertEqual(order.partner_shipping_id.street, '1 Main Street')
        self.assertEqual(order.partner_shipping_id.country_id.code, 'FR')
        # the product line, plus the shipping service line Shopify sends
        self.assertEqual(len(order.order_line), 2)
        product_line = order.order_line.filtered(lambda l: l.ms_line_gid)
        self.assertEqual(product_line.product_uom_qty, 2)
        self.assertAlmostEqual(product_line.price_unit, 59.90)
        self.assertAlmostEqual(order.amount_total, 119.80)
        self.assertEqual(order.ms_financial_status, 'paid')
        self.assertEqual(order.ms_gateway, 'shopify_payments')
        self.assertAlmostEqual(order.ms_amount_received, 119.80)
        self.assertEqual(order.ms_location_id, self.location)
        self.assertTrue(order.picking_ids, "a delivery must be created")

    def test_order_imported_once(self):
        node = self._order_node()
        self.assertEqual(self.store._process_order(node), 'created')
        self.assertIsNone(self.store._process_order(node), "an unchanged order must be ignored")
        self.assertEqual(self.env['sale.order'].search_count([('ms_order_gid', '!=', False)]), 1)

    def test_unpaid_order_can_be_skipped(self):
        self.store.order_status_filter = 'paid'
        node = self._order_node()
        node['displayFinancialStatus'] = 'PENDING'
        self.assertIsNone(self.store._process_order(node))
        self.assertEqual(self.env['sale.order'].search_count([('ms_order_gid', '!=', False)]), 0)

    def test_taxes_matched_by_rate(self):
        self.store.order_tax_mode = 'shopify'
        node = self._order_node()
        node['lineItems']['nodes'][0]['taxLines'] = [{'title': 'VAT', 'ratePercentage': 15.0,
                                                     'priceSet': {'shopMoney': {'amount': '17.97'}}}]
        node['currentTotalPriceSet'] = {'shopMoney': {'amount': '137.77'}}
        node['totalReceivedSet'] = {'shopMoney': {'amount': '137.77'}}
        self.store._process_order(node)
        order = self.env['sale.order'].search([('ms_order_gid', '!=', False)], limit=1)
        line = order.order_line.filtered(lambda l: l.ms_line_gid)
        # the 15% Shopify tax is matched to the Odoo sale tax of the same rate
        self.assertEqual(line[self._tax_field(line)].amount, 15.0)
        self.assertAlmostEqual(order.amount_total, 137.77, places=2)

    def test_customer_reused_for_second_order(self):
        self.store._process_order(self._order_node('#1001'))
        self.store._process_order(self._order_node('#1002'))
        partners = self.env['res.partner'].search([('email', '=', 'jane@example.com')])
        self.assertEqual(len(partners), 1, "the same Shopify customer must give one contact")
        self.assertEqual(partners.child_ids, self.env['res.partner'],
                         "billing and shipping addresses are the same: no child contact needed")


class TestStock(MirakConnectorCase):

    def test_stock_sent_to_shopify(self):
        template = self._product('Stocked', 10.0, sku='STK-1')
        template._ms_mark_dirty({'all'})
        self.store._push_products(template)
        link = self.env['mirak.shopify.product.map'].search([('store_id', '=', self.store.id)], limit=1)
        self.env['stock.quant']._update_available_quantity(
            template.product_variant_id, self.warehouse.lot_stock_id, 12)
        self.client.levels[link.inventory_item_gid] = 0
        self.store._sync_inventory()
        payload = self._last_call('inventorySetQuantities')
        self.assertIsNotNone(payload, "the quantity must be pushed to Shopify")
        self.assertEqual(payload['input']['quantities'][0]['quantity'], 12)

    def test_shopify_stock_change_lands_in_odoo(self):
        template = self._product('Imported', 10.0, sku='IMP-1')
        template._ms_mark_dirty({'all'})
        self.store._push_products(template)
        link = self.env['mirak.shopify.product.map'].search([('store_id', '=', self.store.id)], limit=1)
        link.write({'qty_initialized': True, 'last_qty': 5})
        self.client.levels[link.inventory_item_gid] = 3
        self.store._sync_inventory()
        quant = self.env['stock.quant'].search([
            ('product_id', '=', template.product_variant_id.id),
            ('location_id', '=', self.warehouse.lot_stock_id.id)], limit=1)
        self.assertAlmostEqual(quant.quantity, -2.0, msg="3 in Shopify instead of 5: -2 in Odoo")
        # the negative Odoo quantity cannot be published: Shopify is set back to 0
        payload = self._last_call('inventorySetQuantities')
        self.assertEqual(payload['input']['quantities'][0]['quantity'], 0)
        self.assertEqual(link.last_qty, 0)


class TestWebhook(MirakConnectorCase):

    def test_signature_is_checked(self):
        self.store.write({'client_secret': 'shpss_secret'})
        body = json.dumps({'admin_graphql_api_id': 'gid://shopify/Order/1'}).encode()
        signature = base64.b64encode(
            hmac.new(b'shpss_secret', body, hashlib.sha256).digest()).decode()
        self.assertFalse(self.store._webhook_receive('ORDERS_CREATE', body, 'wrong'))
        self.assertTrue(self.store._webhook_receive('ORDERS_CREATE', body, signature, 'wh-1'))
        event = self.env['mirak.shopify.webhook.event'].search([('store_id', '=', self.store.id)])
        self.assertEqual(len(event), 1)
        self.assertEqual(event.kind, 'orders')
        # Shopify retries: the same X-Shopify-Webhook-Id is not stored twice
        self.assertTrue(self.store._webhook_receive('ORDERS_CREATE', body, signature, 'wh-1'))
        self.assertEqual(self.env['mirak.shopify.webhook.event'].search_count(
            [('store_id', '=', self.store.id)]), 1)

    def test_webhook_needs_https(self):
        self.store.webhook_base_url = 'http://shop.example.com'
        with self.assertRaises(Exception):
            self.store._webhook_uri()


class TestSettings(MirakConnectorCase):

    def test_interval_cannot_be_zero(self):
        with self.assertRaises(Exception):
            self.store.sync_interval = 0

    def test_percentage_bounds(self):
        with self.assertRaises(Exception):
            self.store.stock_percent = 150

    def test_pricelist_required(self):
        with self.assertRaises(Exception):
            self.store.price_source = 'pricelist'

    def test_domain_cannot_change_with_links(self):
        self.store._import_product(self._shopify_product())
        with self.assertRaises(Exception):
            self.store.shop_domain = 'other.myshopify.com'
