# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
import os

from lxml import etree

from odoo import Command, fields

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.mirak_uae_einvoice.tools.ubl_writer import NS

XPATH_NS = {'cac': NS['cac'], 'cbc': NS['cbc'], 'inv': NS['invoice'], 'cn': NS['credit_note']}


class MirakAeCommon(AccountTestInvoicingCommon):
    """A UAE company (AED, generic chart) with its own PINT-AE taxes: no dependency on l10n_ae."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass(chart_template_ref='generic_coa')
        cls.env.company.country_id = cls.env.ref('base.ae')
        cls.env.user.groups_id |= cls.env.ref('mirak_uae_einvoice.group_mirak_ae_manager')
        cls.company = cls.env.company
        cls.company.partner_id.write({'street': 'Sheikh Zayed Road 1', 'city': 'Dubai'})
        cls.company.write({
            'mirak_ae_trn': '100123456789003',
            'mirak_ae_emirate': 'DXB',
            'mirak_ae_legal_id': 'TL-778899',
            'mirak_ae_legal_id_type': 'TL',
            'mirak_ae_legal_authority': 'Dubai Department of Economy and Tourism',
            'phone': '+97140000000',
            'email': 'billing@seller.ae',
        })
        Tax = cls.env['account.tax']
        common = {'type_tax_use': 'sale', 'amount_type': 'percent', 'company_id': cls.company.id}
        cls.tax_s = Tax.create({**common, 'name': 'VAT 5% (test)', 'amount': 5.0})
        cls.tax_s.mirak_ae_category = 'S'
        cls.tax_z = Tax.create({**common, 'name': 'Zero rated (test)', 'amount': 0.0})
        cls.tax_z.mirak_ae_category = 'Z'
        cls.tax_e = Tax.create({**common, 'name': 'Exempt (test)', 'amount': 0.0})
        cls.tax_e.write({'mirak_ae_category': 'E', 'mirak_ae_exemption_code': 'DL8.46.2'})
        cls.tax_o = Tax.create({**common, 'name': 'Out of scope (test)', 'amount': 0.0})
        cls.tax_o.mirak_ae_category = 'O'
        cls.tax_rc = Tax.create({**common, 'name': 'Reverse charge (test)', 'amount': 0.0})
        cls.tax_rc.mirak_ae_category = 'AE'
        cls.tax_purchase_s = Tax.create({**common, 'type_tax_use': 'purchase', 'name': 'Purchase VAT 5% (test)',
                                         'amount': 5.0})
        cls.tax_purchase_s.mirak_ae_category = 'S'

        ae = cls.env.ref('base.ae')
        cls.buyer = cls.env['res.partner'].create({
            'name': 'Noor Electronics LLC', 'is_company': True, 'country_id': ae.id,
            'street': 'Corniche Road 5', 'city': 'Abu Dhabi', 'mirak_ae_emirate': 'AUH',
            'vat': '100987654321003', 'mirak_ae_legal_id': 'CN-1234567', 'mirak_ae_legal_id_type': 'TL',
            'mirak_ae_legal_authority': 'Abu Dhabi Department of Economic Development',
        })
        cls.foreign_buyer = cls.env['res.partner'].create({
            'name': 'Acme Inc', 'is_company': True, 'country_id': cls.env.ref('base.us').id,
            'state_id': cls.env.ref('base.state_us_5').id, 'street': '1 Market St', 'city': 'San Francisco',
        })
        cls.product_goods = cls._create_product(name='Laptop', default_code='LAP-1', barcode='0999990000017',
                                                lst_price=1000.0, taxes_id=cls.tax_s)
        cls.product_goods.product_tmpl_id.write({'mirak_ae_item_type': 'G', 'mirak_ae_hs_code': '847130',
                                                 'mirak_ae_goods_type': 'DL8.48.8.2'})
        cls.product_service = cls._create_product(name='Consulting hour', lst_price=200.0, taxes_id=cls.tax_s)
        cls.product_service.product_tmpl_id.write({'mirak_ae_item_type': 'S', 'mirak_ae_sac_code': '998311'})

    @classmethod
    def _ae_invoice(cls, lines, move_type='out_invoice', partner=None, currency=None, post=True, **values):
        move = cls.env['account.move'].create({
            'move_type': move_type,
            'partner_id': (partner or cls.buyer).id,
            'invoice_date': fields.Date.to_date('2026-10-01'),
            'currency_id': (currency or cls.company.currency_id).id,
            'invoice_line_ids': [Command.create({
                'product_id': product.id, 'quantity': qty, 'price_unit': price,
                'discount': discount, 'tax_ids': [Command.set(tax.ids)],
            }) for product, qty, price, tax, discount in lines],
            **values,
        })
        if post:
            move.action_post()
        return move

    def _xml(self, move):
        move.action_mirak_ae_generate_xml()
        content = move._mirak_ae_xml_content()
        dump_dir = os.environ.get('MIRAK_AE_DUMP_DIR')
        if dump_dir:    # development aid: keep the generated files for the official Schematron run
            with open(os.path.join(dump_dir, '%s.xml' % self._testMethodName), 'wb') as dump:
                dump.write(content)
        return etree.fromstring(content)

    def _text(self, tree, xpath):
        return [node.text for node in tree.xpath(xpath, namespaces=XPATH_NS)]
