# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
import base64
import re
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

from ..tools import pint_ae, schemas, ubl_writer

STATES = [
    ('draft', 'Not sent'),
    ('ready', 'Ready'),
    ('sent', 'Sent'),
    ('accepted', 'Accepted'),
    ('rejected', 'Rejected'),
    ('error', 'Error'),
]
# Odoo units of measure -> UN/ECE Recommendation 20 (UNECERec20.gc). Anything else is sent as C62 ("one").
UNECE_UOM = {
    'uom.product_uom_unit': 'C62', 'uom.product_uom_dozen': 'DZN', 'uom.product_uom_kgm': 'KGM',
    'uom.product_uom_gram': 'GRM', 'uom.product_uom_ton': 'TNE', 'uom.product_uom_lb': 'LBR',
    'uom.product_uom_oz': 'ONZ', 'uom.product_uom_hour': 'HUR', 'uom.product_uom_day': 'DAY',
    'uom.product_uom_litre': 'LTR', 'uom.product_uom_cubic_meter': 'MTQ', 'uom.product_uom_meter': 'MTR',
    'uom.product_uom_km': 'KMT', 'uom.product_uom_cm': 'CMT', 'uom.product_uom_millimeter': 'MMT',
    'uom.product_uom_square_meter': 'MTK', 'uom.product_uom_inch': 'INH', 'uom.product_uom_foot': 'FOT',
    'uom.product_uom_mile': 'SMI', 'uom.product_uom_gal': 'GLL',
}
# res.country.state codes of base.ae -> PINT-AE emirate codes (ibr-128-ae)
EMIRATE_FROM_STATE = {'AZ': 'AUH', 'DU': 'DXB', 'SH': 'SHJ', 'AJ': 'AJM', 'UQ': 'UAQ', 'RK': 'RAK', 'FU': 'FUJ'}
FLAG_FIELDS =['mirak_ae_tt_%s' % key for key, _label in pint_ae.TRANSACTION_FLAGS]


class AccountMove(models.Model):
    _inherit = 'account.move'

    mirak_ae_applicable = fields.Boolean(compute='_compute_mirak_ae_applicable')
    mirak_ae_state = fields.Selection(STATES, string='E-Invoice Status', copy=False, tracking=True, index=True)
    mirak_ae_uuid = fields.Char(string='E-Invoice UUID', copy=False, readonly=True,
                                help='BTAE-07, the unique identifier of this e-invoice.')
    mirak_ae_xml_id = fields.Many2one('ir.attachment', string='PINT-AE XML', copy=False, readonly=True)
    mirak_ae_reference = fields.Char(string='Provider Reference', copy=False, readonly=True)
    mirak_ae_attempts = fields.Integer(string='Sending Attempts', copy=False, readonly=True)
    mirak_ae_message = fields.Text(string='Last Message', copy=False, readonly=True)
    mirak_ae_last_attempt = fields.Datetime(string='Last Attempt', copy=False, readonly=True)
    mirak_ae_issues = fields.Json(string='Pre-validation Issues', copy=False, readonly=True)
    mirak_ae_issue_count = fields.Integer(compute='_compute_mirak_ae_issue_count')
    mirak_ae_error_count = fields.Integer(compute='_compute_mirak_ae_issue_count')

    # Invoice transaction type (BTAE-02): eight flags, in the order of transactiontype.gc
    mirak_ae_tt_free_trade_zone = fields.Boolean(string='Free Trade Zone')
    mirak_ae_tt_deemed_supply = fields.Boolean(string='Deemed Supply')
    mirak_ae_tt_margin_scheme = fields.Boolean(string='Profit Margin Scheme')
    mirak_ae_tt_summary_invoice = fields.Boolean(string='Summary Invoice')
    mirak_ae_tt_continuous_supply = fields.Boolean(string='Continuous Supply')
    mirak_ae_tt_agent_billing = fields.Boolean(string='Disclosed Agent Billing')
    mirak_ae_tt_ecommerce = fields.Boolean(string='Supply through E-Commerce')
    mirak_ae_tt_exports = fields.Boolean(string='Exports', compute='_compute_mirak_ae_tt_exports', store=True,
                                         readonly=False)
    mirak_ae_beneficiary_trn = fields.Char(string='Beneficiary TRN',
                                           help='BTAE-01, required for a supply involving a free trade zone.')
    mirak_ae_principal_trn = fields.Char(string='Principal TRN',
                                         help='BTAE-14, required for disclosed agent billing.')
    mirak_ae_period_start = fields.Date(string='Invoicing Period From')
    mirak_ae_period_end = fields.Date(string='Invoicing Period To')
    mirak_ae_billing_frequency = fields.Selection(pint_ae.BILLING_FREQUENCIES, string='Frequency of Billing')
    mirak_ae_credit_reason = fields.Selection(pint_ae.CREDIT_REASONS, string='Credit Note Reason', copy=False,
                                              help='BTAE-03, mandatory on credit notes (rule ibr-158-ae).')
    mirak_ae_payment_means = fields.Selection(pint_ae.PAYMENT_MEANS, string='Payment Means',
                                              compute='_compute_mirak_ae_payment_means', store=True, readonly=False)

    # ------------------------------------------------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------------------------------------------------
    @api.depends('move_type', 'company_id.mirak_ae_trn')
    def _compute_mirak_ae_applicable(self):
        for move in self:
            move.mirak_ae_applicable = move.move_type in ('out_invoice', 'out_refund') and \
                bool(move.company_id.mirak_ae_trn)

    @api.depends('mirak_ae_issues')
    def _compute_mirak_ae_issue_count(self):
        for move in self:
            move.mirak_ae_issue_count = len(move.mirak_ae_issues or [])
            move.mirak_ae_error_count = len([i for i in move.mirak_ae_issues or [] if i.get('level') == 'error'])

    @api.depends('partner_id.country_id', 'company_id.country_id')
    def _compute_mirak_ae_tt_exports(self):
        for move in self:
            country = move.partner_id.commercial_partner_id.country_id
            move.mirak_ae_tt_exports = bool(country) and country.code != 'AE' and \
                move.move_type in ('out_invoice', 'out_refund')

    @api.depends('partner_bank_id')
    def _compute_mirak_ae_payment_means(self):
        for move in self:
            if move.partner_bank_id:
                move.mirak_ae_payment_means = '30'
            elif not move.mirak_ae_payment_means:
                move.mirak_ae_payment_means = '1'

    # ------------------------------------------------------------------------------------------------------------
    # PINT-AE values
    # ------------------------------------------------------------------------------------------------------------
    def _mirak_ae_transaction_code(self):
        return pint_ae.transaction_type_code({key: self['mirak_ae_tt_%s' % key]
                                              for key, _label in pint_ae.TRANSACTION_FLAGS})

    def _mirak_ae_invoice_lines(self):
        return self.invoice_line_ids.filtered(lambda line: line.display_type == 'product')

    @api.model
    def _mirak_ae_line_tax(self, line):
        """The VAT tax of a line (the first tax that carries a PINT-AE category)."""
        return line.tax_ids.filtered('mirak_ae_category')[:1]

    @api.model
    def _mirak_ae_uom_code(self, uom):
        if not uom:
            return 'C62'
        return UNECE_UOM.get(uom.get_external_id().get(uom.id), 'C62')

    @api.model
    def _mirak_ae_address(self, partner, emirate=None):
        country = partner.country_id.code
        if country == 'AE':
            subentity = emirate or partner.mirak_ae_emirate or EMIRATE_FROM_STATE.get(partner.state_id.code)
        else:
            subentity = partner.state_id.code
        return {
            'street': partner.street, 'street2': partner.street2, 'city': partner.city, 'zip': partner.zip,
            'subentity': subentity, 'country': country,
        }

    @api.model
    def _mirak_ae_legal(self, record):
        if not record.mirak_ae_legal_id:
            return {}
        return {'id': record.mirak_ae_legal_id, 'type': record.mirak_ae_legal_id_type,
                'authority': record.mirak_ae_legal_authority}

    def _mirak_ae_supplier_values(self):
        company = self.company_id
        partner = company.partner_id
        return {
            'endpoint': company.mirak_ae_endpoint_id, 'scheme': pint_ae.UAE_EAS,
            'name': company.name, 'trade_name': company.name,
            'address': self._mirak_ae_address(partner, company.mirak_ae_emirate),
            'trn': company.mirak_ae_trn, 'legal': self._mirak_ae_legal(company),
            'contact': {'name': False, 'phone': company.phone, 'email': company.email},
        }

    def _mirak_ae_customer_values(self):
        partner = self.partner_id.commercial_partner_id
        if partner.mirak_ae_endpoint_id:
            endpoint, scheme = partner.mirak_ae_endpoint_id, partner.mirak_ae_endpoint_scheme or pint_ae.UAE_EAS
        elif self.mirak_ae_tt_exports:
            endpoint, scheme = pint_ae.ENDPOINT_EXPORTS, pint_ae.UAE_EAS
        else:
            endpoint, scheme = pint_ae.ENDPOINT_BUYER_NOT_ONBOARDED, pint_ae.UAE_EAS
        contact = self.partner_id
        return {
            'endpoint': endpoint, 'scheme': scheme,
            'name': partner.name, 'trade_name': False,
            'address': self._mirak_ae_address(partner),
            'trn': partner.mirak_ae_trn, 'legal': self._mirak_ae_legal(partner),
            'contact': {'name': contact.name if contact != partner else False,
                        'phone': contact.phone, 'email': contact.email},
        }

    def _mirak_ae_category_values(self, tax):
        """(category, percent, exemption code, exemption reason) for a line tax."""
        category = tax.mirak_ae_category
        if category == 'S':
            return category, tax.amount, None, None
        if category == 'Z':
            return category, 0.0, None, None
        if category == 'AE':
            # reverse charge: the rate stays 5 and the VAT amount is zero (official RCM example)
            return category, tax.amount or 5.0, None, None
        if category == 'E':
            code = tax.mirak_ae_exemption_code
            return category, None, code, dict(pint_ae.EXEMPTION_CODES).get(code)
        return category, None, None, None

    def _mirak_ae_line_values(self, line, index):
        currency = self.currency_id
        tax = self._mirak_ae_line_tax(line)
        category, percent, exemption_code, exemption_reason = self._mirak_ae_category_values(tax)
        subtotal = line.price_subtotal
        quantity = line.quantity
        if currency.compare_amounts(subtotal, 0) < 0 and quantity > 0:
            quantity = -quantity            # a negative line (e.g. a discount line) keeps a positive price
        discount = min(line.discount or 0.0, 100.0)
        if quantity and discount < 100:
            gross = abs(subtotal / (quantity * (1 - discount / 100.0)))
        else:
            gross = abs(line.price_unit)
        gross = float_round(gross, precision_digits=6)
        price_discount = float_round(gross * discount / 100.0, precision_digits=6)
        net = float_round(gross - price_discount, precision_digits=6)
        if quantity and currency.compare_amounts(quantity * net, subtotal) != 0 and discount < 100:
            # the rounded unit price does not give back the subtotal: carry the exact net price
            net = float_round(subtotal / quantity, precision_digits=6)
            price_discount = float_round(gross - net, precision_digits=6)
            if price_discount < 0:
                gross, price_discount = net, 0.0
        if category in ('S',):
            line_tax = currency.round(line.price_total - subtotal)
        else:
            line_tax = 0.0
        product = line.product_id
        template = product.product_tmpl_id
        description = (line.name or product.display_name or '').strip()
        name = product.name or (description.splitlines()[0] if description else '') or _('Item')
        values = {
            'id': str(index),
            'quantity': quantity,
            'uom': self._mirak_ae_uom_code(line.product_uom_id),
            'line_amount': subtotal,
            'description': description or name,
            'name': name[:200],
            'seller_code': product.default_code,
            'standard_id': product.barcode,
            'item_type': template.mirak_ae_item_type,
            'hs': template.mirak_ae_hs_code if template.mirak_ae_item_type in ('G', 'B') else False,
            'sac': template.mirak_ae_sac_code if template.mirak_ae_item_type in ('S', 'B') else False,
            'nature': template.mirak_ae_goods_type if category == 'AE' else False,
            'category': category,
            'percent': percent,
            'exemption_code': exemption_code,
            'exemption_reason': exemption_reason,
            'gross_price': gross,
            'price_discount': price_discount,
            'net_price': net,
            'amount_incl': subtotal + line_tax,
            'line_tax': None if category == 'E' else line_tax,
        }
        return values

    def _mirak_ae_tax_by_category(self):
        """Tax amounts of the move per PINT-AE category, in document currency, from the posted tax lines."""
        sign = -1 if self.move_type == 'out_invoice' else 1
        result = {}
        for tax_line in self.line_ids.filtered('tax_line_id'):
            key = tax_line.tax_line_id.mirak_ae_category or 'S'
            result[key] = result.get(key, 0.0) + sign * tax_line.amount_currency
        return result

    def _mirak_ae_prepare_values(self):
        self.ensure_one()
        currency = self.currency_id
        is_invoice = self.move_type == 'out_invoice'
        lines = [self._mirak_ae_line_values(line, index)
                 for index, line in enumerate(self._mirak_ae_invoice_lines(), start=1)]
        categories = {line['category'] for line in lines}
        only_exempt_or_oos = bool(categories) and categories <= {'E', 'O'}
        if is_invoice:
            type_code = '480' if only_exempt_or_oos else '380'
        else:
            type_code = '81' if only_exempt_or_oos else '381'

        # VAT breakdown, one per category / rate / exemption reason
        groups = {}
        for line in lines:
            key = (line['category'], line['percent'], line['exemption_code'])
            group = groups.setdefault(key, {'category': line['category'], 'percent': line['percent'],
                                            'exemption_code': line['exemption_code'],
                                            'exemption_reason': line['exemption_reason'],
                                            'taxable': 0.0, 'tax': 0.0, 'line_tax': 0.0})
            group['taxable'] += line['line_amount']
            group['line_tax'] += line['line_tax'] or 0.0
        posted_tax = self._mirak_ae_tax_by_category()
        for group in groups.values():
            group['taxable'] = currency.round(group['taxable'])
            if group['category'] == 'S':
                same = [g for g in groups.values() if g['category'] == 'S']
                # one standard-rate group: use the accounting amount; several rates: the line amounts
                group['tax'] = currency.round(posted_tax.get('S', group['line_tax']) if len(same) == 1
                                              else group['line_tax'])
        subtotals = sorted(groups.values(), key=lambda g: (g['category'], g['percent'] or 0))
        tax_amount = currency.round(sum(g['tax'] for g in subtotals))
        line_total = currency.round(sum(line['line_amount'] for line in lines))
        tax_inclusive = currency.round(line_total + tax_amount)
        rounding = currency.round(self.amount_total - tax_inclusive)
        totals = {
            'line': line_total, 'tax_exclusive': line_total, 'tax_inclusive': tax_inclusive,
            'rounding': 0.0 if currency.is_zero(rounding) else rounding,
            'payable': currency.round(tax_inclusive + rounding),
        }

        values = {
            'kind': 'invoice' if is_invoice else 'credit_note',
            'customization_id': pint_ae.CUSTOMIZATION_ID,
            'profile_id': pint_ae.PROFILE_ID,
            'transaction_code': self._mirak_ae_transaction_code(),
            'number': self.name,
            'uuid': self.mirak_ae_uuid,
            'issue_date': fields.Date.to_string(self.invoice_date or fields.Date.context_today(self)),
            'due_date': fields.Date.to_string(self.invoice_date_due) if is_invoice and self.invoice_date_due and
            totals['payable'] > 0 else None,
            'type_code': type_code,
            'note': self.mirak_ae_billing_frequency == 'OTH' and (self.narration and re.sub(
                r'<[^>]+>', ' ', str(self.narration)).strip() or _('Other billing frequency')) or None,
            'currency': currency.name,
            'buyer_reference': self.ref,
            'period': None,
            'credit_reason': self.mirak_ae_credit_reason if not is_invoice else None,
            'billing_references': [],
            'supplier': self._mirak_ae_supplier_values(),
            'customer': self._mirak_ae_customer_values(),
            'beneficiary_id': self.mirak_ae_tt_free_trade_zone and self.mirak_ae_beneficiary_trn,
            'principal_id': self.mirak_ae_tt_agent_billing and self.mirak_ae_principal_trn,
            'tax_amount': tax_amount,
            'tax_subtotals': subtotals,
            'totals': totals,
            'lines': lines,
        }
        if self.mirak_ae_period_start or self.mirak_ae_period_end or self.mirak_ae_billing_frequency:
            values['period'] = {
                'start': fields.Date.to_string(self.mirak_ae_period_start) if self.mirak_ae_period_start else None,
                'end': fields.Date.to_string(self.mirak_ae_period_end) if self.mirak_ae_period_end else None,
                'frequency': self.mirak_ae_billing_frequency,
            }
        if not is_invoice and self.reversed_entry_id:
            values['billing_references'] = [{
                'id': self.reversed_entry_id.name,
                'date': fields.Date.to_string(self.reversed_entry_id.invoice_date),
            }]
        if is_invoice and not self.mirak_ae_tt_deemed_supply:
            values['payment_means'] = self._mirak_ae_payment_means_values()
        if self.invoice_payment_term_id:
            values['payment_terms'] = self.invoice_payment_term_id.name
        if self.mirak_ae_tt_exports or self.mirak_ae_tt_ecommerce:
            shipping = self.partner_shipping_id or self.partner_id
            values['delivery'] = {
                'date': fields.Date.to_string(self.delivery_date) if self.delivery_date else None,
                'address': self._mirak_ae_address(shipping),
                'incoterm': self.invoice_incoterm_id.code,
            }
        if currency.name != 'AED':
            aed = self.env.ref('base.AED')
            values['tax_currency'] = 'AED'
            values['exchange_rate'] = self._mirak_ae_aed_rate()
            company_currency = self.company_id.currency_id
            if company_currency == aed:
                values['tax_amount_aed'] = abs(self.amount_tax_signed)
                values['aed_total'] = abs(self.amount_total_signed)
            else:
                rate = values['exchange_rate']
                values['tax_amount_aed'] = aed.round(tax_amount * rate)
                values['aed_total'] = aed.round(self.amount_total * rate)
        return values

    def _mirak_ae_aed_rate(self):
        """AED per one unit of the invoice currency (BTAE-04), at most 6 decimals (rule ibr-002-ae)."""
        aed = self.env.ref('base.AED')
        if self.company_id.currency_id == aed and self.invoice_currency_rate:
            rate = 1.0 / self.invoice_currency_rate
        else:
            rate = self.currency_id._get_conversion_rate(self.currency_id, aed, self.company_id,
                                                         self.invoice_date or fields.Date.context_today(self))
        return float_round(rate, precision_digits=6)

    def _mirak_ae_payment_means_values(self):
        code = self.mirak_ae_payment_means or '1'
        values = {'code': code, 'name': dict(pint_ae.PAYMENT_MEANS).get(code),
                  'payment_id': self.payment_reference or self.name}
        bank = self.partner_bank_id
        if code == '30' and bank:
            account = re.sub(r'\s', '', bank.acc_number or '').upper()
            # acc_type is 'iban' only with base_iban installed: recognise the IBAN format as well
            is_iban = bank.acc_type == 'iban' or bool(re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', account))
            values.update({
                'account': account,
                'account_scheme': 'IBAN' if is_iban else None,
                'account_name': bank.acc_holder_name or bank.partner_id.name,
                'bic': bank.bank_id.bic,
            })
        return values

    # ------------------------------------------------------------------------------------------------------------
    # XML
    # ------------------------------------------------------------------------------------------------------------
    def _mirak_ae_filename(self):
        return '%s_pint_ae.xml' % re.sub(r'[^\w.-]+', '_', self.name or 'draft')

    def _mirak_ae_build_xml(self):
        """Return the PINT-AE XML (bytes) of this posted customer invoice or credit note."""
        self.ensure_one()
        if not self.mirak_ae_uuid:
            self.mirak_ae_uuid = str(uuid.uuid4())
        return ubl_writer.render(self._mirak_ae_prepare_values())

    def _mirak_ae_attach_xml(self, xml_content):
        self.ensure_one()
        values = {
            'name': self._mirak_ae_filename(),
            'raw': xml_content,
            'mimetype': 'application/xml',
            'res_model': self._name,
            'res_id': self.id,
            'res_field': False,
            'company_id': self.company_id.id,
            'description': 'PINT-AE 1.0.4 e-invoice',
        }
        if self.mirak_ae_xml_id:
            self.mirak_ae_xml_id.write(values)
        else:
            self.mirak_ae_xml_id = self.env['ir.attachment'].create(values)
        return self.mirak_ae_xml_id

    def action_mirak_ae_generate_xml(self):
        """Build the XML, check it against the UBL 2.1 schema and attach it to the invoice."""
        for move in self:
            if not move.mirak_ae_applicable:
                raise UserError(_('%s is not a customer invoice or credit note of a company with a TRN.',
                                  move.display_name))
            if move.state != 'posted':
                raise UserError(_('Post %s before generating its e-invoice.', move.display_name))
            xml_content = move._mirak_ae_build_xml()
            errors = schemas.validate_xsd(xml_content)
            if errors:
                raise UserError(_('The generated XML does not match the UBL 2.1 schema:\n%s', '\n'.join(errors[:10])))
            move._mirak_ae_attach_xml(xml_content)
            if not move.mirak_ae_state:
                move.mirak_ae_state = 'draft'
        return True

    def action_mirak_ae_download_xml(self):
        self.ensure_one()
        if not self.mirak_ae_xml_id:
            self.action_mirak_ae_generate_xml()
        return {'type': 'ir.actions.act_url', 'target': 'download',
                'url': '/web/content/%s?download=true' % self.mirak_ae_xml_id.id}

    def _mirak_ae_xml_content(self):
        self.ensure_one()
        return base64.b64decode(self.mirak_ae_xml_id.datas) if self.mirak_ae_xml_id else b''
