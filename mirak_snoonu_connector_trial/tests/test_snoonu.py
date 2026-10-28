# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

MODULE = 'odoo.addons.mirak_snoonu_connector_trial.models.snoonu_account'


class FakeResponse:
    def __init__(self, status, text='{"ok": true}'):
        self.status_code, self.text = status, text


@tagged('post_install', '-at_install')
class TestSnoonu(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.wh1 = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.wh2 = cls.env['stock.warehouse'].create({'name': 'Snoonu WH2', 'code': 'SNW2'})
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'Snoonu promo', 'currency_id': cls.company.currency_id.id,
            'item_ids': [(0, 0, {'compute_price': 'percentage', 'percent_price': 10, 'applied_on': '3_global'})],
        })
        cls.account = cls.env['snoonu.account'].create({
            'name': 'Test brand', 'business_unit_id': 'BU1', 'api_email': 'me@example.com', 'api_key': 'KEY',
            'branch_ids': [
                (0, 0, {'name': 'Main', 'branch_ref': 'BR1', 'warehouse_id': cls.wh1.id, 'use_defaults': True}),
                (0, 0, {'name': 'Airport', 'branch_ref': 'BR2', 'warehouse_id': cls.wh2.id, 'use_defaults': False,
                        'pricelist_id': cls.pricelist.id, 'stock_buffer': 2}),
            ],
        })
        cls.products = cls.env['product.product'].create([
            {'name': f'Perfume {i}', 'default_code': f'SKU{i}', 'list_price': 100 + i, 'type': 'product'}
            for i in range(5)
        ])
        Quant = cls.env['stock.quant']
        for product in cls.products:
            Quant._update_available_quantity(product, cls.wh1.lot_stock_id, 7.6)
            Quant._update_available_quantity(product, cls.wh2.lot_stock_id, 3)
        cls.maps = cls.env['snoonu.product'].create([
            {'account_id': cls.account.id, 'product_id': p.id} for p in cls.products])

    def _patch(self, *statuses):
        responses = iter([FakeResponse(s) for s in statuses])
        calls = []

        def fake_patch(url, data=None, headers=None, timeout=None):
            calls.append({'url': url, 'body': json.loads(data), 'headers': headers})
            return next(responses)
        return patch(f'{MODULE}.requests.patch', side_effect=fake_patch), patch(f'{MODULE}.time.sleep'), calls

    def test_sku_defaults_to_internal_reference(self):
        self.assertEqual(self.maps.mapped('sku'), [f'SKU{i}' for i in range(5)])

    def test_document_structure(self):
        mapping = self.maps[0]
        _m, doc, _h = self.account._build_documents(mapping)[0]
        self.assertEqual(doc['BusinessUnitId'], 'BU1')
        self.assertEqual(doc['SKU'], 'SKU0')
        self.assertEqual(doc['Price'], 100.0)
        self.assertEqual(doc['Discount'], 0.0)
        main, airport = doc['BranchConfigurations']
        # branch using the brand price: no own price, stock floored to an integer
        self.assertEqual(main, {'BranchId': 'BR1', 'UseDefaults': True, 'IsAvailable': True, 'Stock': 7})
        # branch with its own price: pricelist price, stock minus the safety buffer
        self.assertEqual(airport['UseDefaults'], False)
        self.assertEqual(airport['Price'], 90.0)
        self.assertEqual(airport['Stock'], 1)

    def test_pricelist_reduction_as_discount_amount(self):
        self.account.write({'pricelist_id': self.pricelist.id, 'discount_mode': 'pricelist'})
        _m, doc, _h = self.account._build_documents(self.maps[1])[0]
        self.assertEqual(doc['Price'], 101.0)
        self.assertAlmostEqual(doc['Discount'], 10.1)

    def test_hidden_product(self):
        self.maps[0].is_available = False
        self.assertEqual(self.maps[0].state, 'pending')
        _m, doc, _h = self.account._build_documents(self.maps[0])[0]
        self.assertTrue(all(not c['IsAvailable'] for c in doc['BranchConfigurations']))

    def test_request_and_delta(self):
        p1, p2, calls = self._patch(200, 200)
        with p1, p2:
            summary = self.account._sync()
            self.assertEqual(summary['ok'], 5)
            self.assertEqual(len(calls), 1)
            call = calls[0]
            self.assertEqual(call['url'], 'https://partnersintegration.qat.stg.snoonu.com/api/v1.0/Product/BU1/BulkUpdate')
            self.assertEqual(call['headers']['X-AUTH-APIKEY'], 'KEY')
            self.assertEqual(call['headers']['X-Email'], 'me@example.com')
            self.assertEqual(call['body']['Filters'], ['SKU'])
            self.assertEqual(len(call['body']['Documents']), 5)
            self.assertTrue(all(m.state == 'synced' for m in self.maps))
            # nothing changed: nothing sent
            self.assertEqual(self.account._sync()['sent'], 0)
            # one stock change: only that product is sent
            self.env['stock.quant']._update_available_quantity(self.products[3], self.wh1.lot_stock_id, 5)
            summary = self.account._sync()
            self.assertEqual(summary['sent'], 1)
            self.assertEqual(calls[1]['body']['Documents'][0]['SKU'], 'SKU3')
        self.assertEqual(self.account.log_count, 2)

    def test_batches(self):
        self.account.batch_size = 2
        p1, p2, calls = self._patch(200, 200, 200)
        with p1, p2:
            self.account._sync()
        self.assertEqual([len(c['body']['Documents']) for c in calls], [2, 2, 1])

    def test_error_marks_products(self):
        p1, p2, calls = self._patch(400)
        with p1, p2:
            summary = self.account._sync()
        self.assertEqual(summary['failed'], 5)
        self.assertEqual(len(calls), 1)  # 400 is not retried
        self.assertTrue(all(m.state == 'error' and 'HTTP 400' in m.last_error for m in self.maps))
        self.assertEqual(self.account.log_ids[:1].state, 'error')

    def test_retry_on_temporary_error(self):
        p1, p2, calls = self._patch(503, 200)
        with p1, p2:
            summary = self.account._sync()
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary['ok'], 5)

    def test_production_url(self):
        self.account.write({'environment': 'production', 'production_url': 'https://api.example.com/'})
        self.assertEqual(self.account.endpoint_url, 'https://api.example.com/api/v1.0/Product/BU1/BulkUpdate')
        self.account.production_url = 'https://x.example.com/api/v1.0/Product/{businessUnitId}/BulkUpdate'
        self.assertEqual(self.account.endpoint_url, 'https://x.example.com/api/v1.0/Product/BU1/BulkUpdate')

    def test_missing_credentials(self):
        self.account.api_key = False
        with self.assertRaises(UserError):
            self.account._sync()

    def test_add_products_wizard(self):
        new = self.env['product.product'].create([
            {'name': 'With code', 'default_code': 'NEW1', 'type': 'product'},
            {'name': 'Without code', 'type': 'product'},
            {'name': 'Duplicate', 'default_code': 'SKU0', 'type': 'product'},
        ])
        wizard = self.env['snoonu.add.products'].create({
            'account_id': self.account.id, 'selection': 'selected', 'product_ids': [(6, 0, new.ids)]})
        wizard.action_add()
        added = self.env['snoonu.product'].search([('account_id', '=', self.account.id), ('product_id', 'in', new.ids)])
        self.assertEqual(added.mapped('sku'), ['NEW1'])

    def test_cron_switch(self):
        cron = self.env.ref('mirak_snoonu_connector_trial.ir_cron_snoonu_sync')
        self.account.auto_sync = True
        self.assertTrue(cron.active)
        self.account.auto_sync = False
        self.assertFalse(cron.active)
