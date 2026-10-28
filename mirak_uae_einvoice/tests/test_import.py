# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
import base64

from odoo.tests import tagged

from .common import MirakAeCommon


@tagged('post_install', '-at_install', 'mirak_ae')
class TestImport(MirakAeCommon):
    """A PINT-AE document produced by the module is read back as a vendor bill."""

    def _wizard(self, *contents):
        attachments = self.env['ir.attachment'].create([
            {'name': 'vendor_%s.xml' % index, 'datas': base64.b64encode(content)}
            for index, content in enumerate(contents)])
        return self.env['mirak.ae.import.wizard'].create({'attachment_ids': [(6, 0, attachments.ids)]})

    def _document(self, move):
        move.action_mirak_ae_generate_xml()
        return move._mirak_ae_xml_content()

    def test_import_invoice(self):
        sale = self._ae_invoice([(self.product_goods, 2, 1000.0, self.tax_s, 10.0)])
        wizard = self._wizard(self._document(sale))
        wizard.action_import()
        bill = wizard.move_ids
        self.assertEqual(len(bill), 1, wizard.result)
        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.ref, sale.name)
        self.assertEqual(bill.partner_id.mirak_ae_trn, '100123456789003')       # supplier found / created by TRN
        self.assertEqual(bill.invoice_line_ids.product_id, self.product_goods)  # product found by its code
        self.assertEqual(bill.invoice_line_ids.tax_ids, self.tax_purchase_s)    # tax found by its category
        self.assertAlmostEqual(bill.amount_total, sale.amount_total)
        self.assertEqual(bill.mirak_ae_uuid, sale.mirak_ae_uuid)
        # the same document twice is refused
        again = self._wizard(self._document(sale))
        again.action_import()
        self.assertFalse(again.move_ids)
        self.assertIn('Already imported', again.result)

    def test_import_known_supplier_and_credit_note(self):
        supplier = self.env['res.partner'].create({'name': 'Known Supplier', 'is_company': True,
                                                   'mirak_ae_trn': '100123456789003'})
        sale = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)])
        refund = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)], move_type='out_refund',
                                  reversed_entry_id=sale.id, mirak_ae_credit_reason='DL8.61.1.D')
        wizard = self._wizard(self._document(sale), self._document(refund))
        wizard.action_import()
        bill = wizard.move_ids.filtered(lambda m: m.move_type == 'in_invoice')
        bill_refund = wizard.move_ids.filtered(lambda m: m.move_type == 'in_refund')
        self.assertEqual(bill.partner_id, supplier)
        self.assertEqual(bill_refund.partner_id, supplier)
        self.assertEqual(bill_refund.reversed_entry_id, bill)
        self.assertEqual(bill_refund.mirak_ae_credit_reason, 'DL8.61.1.D')

    def test_not_an_einvoice(self):
        wizard = self._wizard(b'<Order xmlns="urn:x"/>', b'not xml')
        wizard.action_import()
        self.assertFalse(wizard.move_ids)
        self.assertEqual(len(wizard.result.splitlines()), 2)
