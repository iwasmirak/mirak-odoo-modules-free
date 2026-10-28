# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo.tests import tagged

from odoo.addons.mirak_uae_einvoice.tools import pint_ae, schemas

from .common import MirakAeCommon


@tagged('post_install', '-at_install', 'mirak_ae')
class TestPintXml(MirakAeCommon):

    def test_standard_invoice(self):
        move = self._ae_invoice([(self.product_goods, 2, 1000.0, self.tax_s, 10.0),
                                 (self.product_service, 3, 200.0, self.tax_s, 0.0)])
        tree = self._xml(move)
        self.assertEqual(schemas.validate_xsd(move._mirak_ae_xml_content()), [])
        self.assertEqual(self._text(tree, 'cbc:CustomizationID'), [pint_ae.CUSTOMIZATION_ID])
        self.assertEqual(self._text(tree, 'cbc:ProfileExecutionID'), ['00000000'])
        self.assertEqual(self._text(tree, 'cbc:InvoiceTypeCode'), ['380'])
        self.assertTrue(move.mirak_ae_uuid)
        self.assertEqual(self._text(tree, 'cbc:UUID'), [move.mirak_ae_uuid])
        self.assertEqual(self._text(tree, 'cac:AccountingSupplierParty//cbc:EndpointID'), ['1001234567'])
        self.assertEqual(self._text(tree, 'cac:AccountingCustomerParty//cbc:CountrySubentity'), ['AUH'])
        # 1800 + 600 = 2400 net, VAT 120
        self.assertEqual(self._text(tree, 'cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount'), ['2400.00'])
        self.assertEqual(self._text(tree, 'cac:TaxTotal/cbc:TaxAmount'), ['120.00'])
        self.assertEqual(self._text(tree, 'cac:LegalMonetaryTotal/cbc:PayableAmount'), ['2520.00'])
        line = tree.xpath('cac:InvoiceLine', namespaces={'cac': self._ns('cac')})[0]
        self.assertEqual(self._text(line, 'cac:Price/cbc:PriceAmount'), ['900.000000'])
        self.assertEqual(self._text(line, 'cac:Price/cac:AllowanceCharge/cbc:BaseAmount'), ['1000.000000'])
        self.assertEqual(self._text(line, 'cac:ItemPriceExtension/cbc:Amount'), ['1890.00'])
        self.assertEqual(self._text(line, 'cac:ItemPriceExtension/cac:TaxTotal/cbc:TaxAmount'), ['90.00'])
        self.assertEqual(self._text(line, 'cac:Item/cac:CommodityClassification/cbc:ItemClassificationCode'),
                         ['847130'])
        self.assertEqual(self._text(tree, 'cac:PaymentMeans/cbc:PaymentMeansCode'), ['1'])
        self.assertTrue(move.mirak_ae_xml_id)
        self.assertEqual(move.mirak_ae_state, 'draft')

    def test_zero_exempt_and_out_of_scope(self):
        move = self._ae_invoice([(self.product_goods, 1, 500.0, self.tax_z, 0.0),
                                 (self.product_service, 1, 300.0, self.tax_e, 0.0),
                                 (self.product_service, 1, 100.0, self.tax_o, 0.0)])
        tree = self._xml(move)
        self.assertEqual(self._text(tree, 'cbc:InvoiceTypeCode'), ['380'])
        cats = {c: self._text(tree, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory[cbc:ID='%s']/cbc:Percent" % c)
                for c in ('Z', 'E', 'O')}
        self.assertEqual(cats, {'Z': ['0'], 'E': [], 'O': []})
        self.assertEqual(self._text(tree, "//cac:TaxCategory[cbc:ID='E']/cbc:TaxExemptionReasonCode"), ['DL8.46.2'])
        # exempt line: no line VAT amount (ibr-163-ae); zero-rated line: zero (ibr-165-ae)
        exempt = tree.xpath("cac:InvoiceLine[cac:Item/cac:ClassifiedTaxCategory/cbc:ID='E']",
                            namespaces={'cac': self._ns('cac'), 'cbc': self._ns('cbc')})[0]
        self.assertFalse(self._text(exempt, 'cac:ItemPriceExtension/cac:TaxTotal/cbc:TaxAmount'))

    def test_credit_transfer_iban(self):
        bank = self.env['res.partner.bank'].create({'partner_id': self.company.partner_id.id,
                                                    'acc_number': 'AE07 0331 2345 6789 0123 456',
                                                    'allow_out_payment': True})
        move = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)], partner_bank_id=bank.id)
        tree = self._xml(move)
        self.assertEqual(self._text(tree, 'cac:PaymentMeans/cbc:PaymentMeansCode'), ['30'])
        self.assertEqual(self._text(tree, 'cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID'),
                         ['AE070331234567890123456'])
        self.assertEqual(tree.xpath('cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID/@schemeID',
                                    namespaces={'cac': self._ns('cac'), 'cbc': self._ns('cbc')}), ['IBAN'])

    def test_exempt_only_is_480(self):
        move = self._ae_invoice([(self.product_service, 1, 300.0, self.tax_e, 0.0)])
        self.assertEqual(self._text(self._xml(move), 'cbc:InvoiceTypeCode'), ['480'])

    def test_reverse_charge(self):
        move = self._ae_invoice([(self.product_goods, 3, 1000.0, self.tax_rc, 0.0)])
        tree = self._xml(move)
        self.assertEqual(self._text(tree, "cac:TaxTotal/cac:TaxSubtotal[cac:TaxCategory/cbc:ID='AE']/cbc:TaxAmount"),
                         ['0.00'])
        self.assertEqual(self._text(tree, "//cac:ClassifiedTaxCategory[cbc:ID='AE']/cbc:Percent"), ['5'])
        self.assertEqual(self._text(tree, '//cac:StandardItemIdentification/cbc:ID'), ['0999990000017'])
        self.assertEqual(self._text(tree, '//cbc:NatureCode'), ['DL8.48.8.2'])

    def test_credit_note(self):
        invoice = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)])
        refund = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)], move_type='out_refund',
                                  reversed_entry_id=invoice.id, mirak_ae_credit_reason='DL8.61.1.D')
        tree = self._xml(refund)
        self.assertEqual(tree.tag, '{%s}CreditNote' % self._ns('credit_note'))
        self.assertEqual(schemas.validate_xsd(refund._mirak_ae_xml_content()), [])
        self.assertEqual(self._text(tree, 'cbc:CreditNoteTypeCode'), ['381'])
        self.assertEqual(self._text(tree, 'cac:DiscrepancyResponse/cbc:ResponseCode'), ['DL8.61.1.D'])
        self.assertEqual(self._text(tree, '//cac:InvoiceDocumentReference/cbc:ID'), [invoice.name])
        self.assertFalse(self._text(tree, 'cac:PaymentMeans/cbc:PaymentMeansCode'))
        self.assertEqual(self._text(tree, 'cac:TaxTotal/cbc:TaxAmount'), ['50.00'])

    def test_foreign_currency(self):
        usd = self.setup_other_currency('USD', rates=[('2020-01-01', 0.272294)])
        move = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)], currency=usd)
        tree = self._xml(move)
        self.assertEqual(schemas.validate_xsd(move._mirak_ae_xml_content()), [])
        self.assertEqual(self._text(tree, 'cbc:TaxCurrencyCode'), ['AED'])
        self.assertEqual(self._text(tree, 'cac:TaxExchangeRate/cbc:CalculationRate'), ['3.672501'])
        self.assertEqual(self._text(tree, "cac:TaxTotal/cbc:TaxAmount[@currencyID='USD']"), ['50.00'])
        self.assertEqual(self._text(tree, "cac:TaxTotal/cbc:TaxAmount[@currencyID='AED']"), ['183.63'])
        self.assertEqual(self._text(tree, "cac:AdditionalDocumentReference[cbc:DocumentTypeCode='aedtotal-incl-vat']"
                                          "/cbc:DocumentDescription"), ['AED 3856.13'])

    def test_exports_use_predefined_endpoint(self):
        move = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_z, 0.0)], partner=self.foreign_buyer)
        self.assertTrue(move.mirak_ae_tt_exports)
        tree = self._xml(move)
        self.assertEqual(self._text(tree, 'cbc:ProfileExecutionID'), ['00000001'])
        self.assertEqual(self._text(tree, 'cac:AccountingCustomerParty//cbc:EndpointID'),
                         [pint_ae.ENDPOINT_EXPORTS])
        self.assertEqual(self._text(tree, 'cac:Delivery//cbc:IdentificationCode'), ['US'])

    def _ns(self, key):
        from odoo.addons.mirak_uae_einvoice.tools.ubl_writer import NS
        return NS[key]
