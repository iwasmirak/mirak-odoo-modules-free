# Copyright 2026 Bouchibane Karim
# License OPL-1 (18.0/legal/licenses.html)
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTrial(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1)
        cls.account = cls.env['snoonu.account'].create({
            'name': 'Trial brand', 'business_unit_id': 'BU-T', 'api_key': 'KEY',
            'branch_ids': [(0, 0, {'name': 'Main', 'branch_ref': 'BR1',
                                    'warehouse_id': cls.warehouse.id})]})

    def test_no_license_key_needed(self):
        """The trial is usable without any key."""
        self.assertTrue(self.account.trial_days_left > 0)
        self.assertEqual(self.account.trial_days_left, 30)

    def test_single_account(self):
        with self.assertRaises(UserError):
            self.env['snoonu.account'].create({'name': 'Second', 'business_unit_id': 'BU-2'})

    def test_product_limit(self):
        products = self.env['product.product'].create([
            {'name': f'Trial %s' % i, 'default_code': f'TR{i}', 'type': 'consu'}
            for i in range(26)])
        values = [{'account_id': self.account.id, 'product_id': p.id} for p in products[:25]]
        self.env['snoonu.product'].create(values)
        with self.assertRaises(UserError):
            self.env['snoonu.product'].create([{'account_id': self.account.id,
                                                 'product_id': products[25].id}])

    def test_expiry(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'mirak_snoonu_trial.start',
            fields.Datetime.to_string(fields.Datetime.now() - timedelta(days=31)))
        self.account.invalidate_recordset(['trial_days_left', 'trial_expiry'])
        self.assertEqual(self.account.trial_days_left, 0)
        with self.assertRaises(UserError):
            self.account._sync()
