# Copyright 2026 Bouchibane Karim
# License OPL-1 (18.0/legal/licenses.html)
"""Checks of the free trial limits."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models import mirak_trial


@tagged('post_install', '-at_install')
class TestTrial(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            mirak_trial.START_KEY, fields.Datetime.now())
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.store = cls.env['mirak.shopify.store'].create({
            'name': 'Trial store', 'shop_domain': 'trial.myshopify.com',
            'warehouse_id': cls.warehouse.id})
        cls.products = cls.env['product.product'].create(
            [{'name': 'Trial product %s' % index} for index in range(mirak_trial.TRIAL_PRODUCTS + 1)])

    def test_license_is_free(self):
        from ..models import mirak_license
        self.assertFalse(mirak_license.LICENSE_KEY_REQUIRED)
        self.store._check_license()          # no key: no error

    def test_single_store(self):
        with self.assertRaises(UserError):
            self.env['mirak.shopify.store'].create({
                'name': 'Second store', 'shop_domain': 'second.myshopify.com'})

    def test_product_limit(self):
        Map = self.env['mirak.shopify.product.map']
        for index, product in enumerate(self.products):
            vals = {
                'store_id': self.store.id,
                'product_id': product.id,
                'shopify_product_gid': 'gid://shopify/Product/%s' % index,
                'shopify_variant_gid': 'gid://shopify/ProductVariant/%s' % index,
            }
            if index < mirak_trial.TRIAL_PRODUCTS:
                Map.create(vals)
            else:
                with self.assertRaises(UserError):
                    Map.create(vals)
        self.assertEqual(self.store._trial_products_used(), mirak_trial.TRIAL_PRODUCTS)

    def test_expiry(self):
        self.store.write({})
        self.assertEqual(self.store.trial_days_left, mirak_trial.TRIAL_DAYS)
        self.env['ir.config_parameter'].sudo().set_param(
            mirak_trial.START_KEY,
            fields.Datetime.now() - timedelta(days=mirak_trial.TRIAL_DAYS + 1))
        self.store.invalidate_recordset(['trial_days_left', 'trial_expiry'])
        self.assertEqual(self.store.trial_days_left, 0)
        with self.assertRaises(UserError):
            self.store._check_license()
