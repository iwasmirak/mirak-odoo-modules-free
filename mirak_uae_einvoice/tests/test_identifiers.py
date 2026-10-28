# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.mirak_uae_einvoice.tools import pint_ae


@tagged('post_install', '-at_install', 'mirak_ae')
class TestIdentifiers(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ae = cls.env.ref('base.ae')

    def test_trn_helpers(self):
        self.assertEqual(pint_ae.normalize_trn(' 100-1234-5678-9003 '), '100123456789003')
        self.assertTrue(pint_ae.is_valid_trn('100123456789003'))
        self.assertFalse(pint_ae.is_valid_trn('10012345678900'))      # 14 digits
        self.assertFalse(pint_ae.is_valid_trn('1001234567890031'))    # 16 digits
        self.assertFalse(pint_ae.is_valid_trn('200123456789003'))     # must start with 1 (ibr-132-ae)
        self.assertFalse(pint_ae.is_valid_trn('100123456789004'))     # must end with 03 (ibr-132-ae)
        self.assertTrue(pint_ae.is_valid_tin('1001234567'))
        self.assertFalse(pint_ae.is_valid_tin('9001234567'))
        self.assertEqual(pint_ae.tin_from_trn('100123456789003'), '1001234567')
        self.assertEqual(pint_ae.tin_from_trn('123'), '')

    def test_transaction_type_code(self):
        self.assertEqual(pint_ae.transaction_type_code({}), '00000000')
        self.assertEqual(pint_ae.transaction_type_code({'exports': True}), '00000001')
        self.assertEqual(pint_ae.transaction_type_code({'free_trade_zone': True, 'continuous_supply': True}),
                         '10001000')

    def test_partner_trn_from_vat_and_endpoint(self):
        partner = self.env['res.partner'].create({
            'name': 'Gulf Trading LLC', 'is_company': True, 'country_id': self.ae.id, 'vat': '100 1234 5678 9003'})
        self.assertEqual(partner.mirak_ae_trn, '100123456789003')
        self.assertEqual(partner.mirak_ae_endpoint_id, '1001234567')
        self.assertEqual(partner.mirak_ae_endpoint_scheme, '0235')
        self.assertEqual(partner.mirak_ae_scope, 'b2b')

    def test_partner_scope_consumer(self):
        person = self.env['res.partner'].create({'name': 'Walk-in customer', 'country_id': self.ae.id})
        self.assertEqual(person.mirak_ae_scope, 'b2c')

    def test_invalid_trn_refused(self):
        with self.assertRaises(ValidationError):
            self.env['res.partner'].create({'name': 'Wrong TRN', 'mirak_ae_trn': '12345'})

    def test_company_tin_and_endpoint(self):
        company = self.env.company
        company.mirak_ae_trn = '100-987-654-321-003'
        self.assertEqual(company.mirak_ae_trn, '100987654321003')
        self.assertEqual(company.mirak_ae_tin, '1009876543')
        self.assertEqual(company.mirak_ae_endpoint_id, '1009876543')
        with self.assertRaises(ValidationError):
            company.mirak_ae_endpoint_id = '12AB'

    def test_mandate_phase_date(self):
        company = self.env.company
        company.mirak_ae_mandate_phase = 'phase_2'
        self.assertEqual(str(company.mirak_ae_mandate_date), '2027-07-01')
        self.assertIsInstance(company._mirak_ae_days_to_mandate(), int)
