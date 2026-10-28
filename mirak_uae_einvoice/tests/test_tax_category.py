# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'mirak_ae')
class TestTaxCategory(TransactionCase):
    """The PINT-AE category is found from the UAE localization external ids, then the name, then the rate."""

    def _tax(self, name, amount, type_tax_use='sale', xml_name=None):
        tax = self.env['account.tax'].create({'name': name, 'amount': amount, 'amount_type': 'percent',
                                              'type_tax_use': type_tax_use})
        if xml_name:
            # same external id shape as the taxes created by l10n_ae for a company
            self.env['ir.model.data'].create({'module': 'account', 'name': xml_name, 'model': 'account.tax',
                                              'res_id': tax.id})
            tax.mirak_ae_category = False
            tax._compute_mirak_ae_category()
        return tax

    def test_l10n_ae_external_ids(self):
        cases = [
            ('99_uae_sale_tax_5_dubai', 5, 'S'),
            ('99_uae_sale_tax_0', 0, 'Z'),
            ('99_uae_export_tax', 0, 'Z'),
            ('99_uae_sale_tax_exempted', 0, 'E'),
            ('99_uae_out_of_scope', 0, 'O'),
            ('99_uae_purchase_tax_reverse_charge', 5, 'AE'),
        ]
        for xml_name, amount, expected in cases:
            tax = self._tax('Tax %s' % xml_name, amount, xml_name=xml_name)
            self.assertEqual(tax.mirak_ae_category, expected, xml_name)

    def test_names_and_rates(self):
        self.assertEqual(self._tax('VAT 5%', 5).mirak_ae_category, 'S')
        self.assertEqual(self._tax('Zero rated 0%', 0).mirak_ae_category, 'Z')
        self.assertEqual(self._tax('Exempt supplies', 0).mirak_ae_category, 'E')
        self.assertEqual(self._tax('Out of scope', 0).mirak_ae_category, 'O')
        self.assertEqual(self._tax('Reverse charge 5%', 5, 'purchase').mirak_ae_category, 'AE')
        self.assertFalse(self._tax('Odd 7%', 7).mirak_ae_category)

    def test_manual_value_kept(self):
        tax = self._tax('VAT 5%', 5)
        tax.mirak_ae_category = 'Z'
        tax.amount = 5.0
        self.assertEqual(tax.mirak_ae_category, 'Z')
