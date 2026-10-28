# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Write a PINT-AE UBL 2.1 Invoice or CreditNote from plain values.

The element order follows the UBL 2.1 schemas (maindoc/UBL-Invoice-2.1.xsd and UBL-CreditNote-2.1.xsd); the
content follows the official PINT-AE 1.0.4 examples. The values are prepared by account.move
(_mirak_ae_prepare_values) so that this module stays free of ORM code and is easy to test.
"""
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

NS = {
    'invoice': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'credit_note': 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}
CAC = '{%s}' % NS['cac']
CBC = '{%s}' % NS['cbc']


def amount(value, digits=2):
    """Decimal string with a fixed number of decimals ('1050.00')."""
    quant = Decimal(1).scaleb(-digits)
    return str(Decimal(str(value or 0)).quantize(quant, rounding=ROUND_HALF_UP))


def number(value, digits=6):
    """Decimal string without useless trailing zeros ('3.67285', '2', '0.5')."""
    text = amount(value, digits)
    return text.rstrip('0').rstrip('.') if '.' in text else text


class _Writer:
    def __init__(self, values):
        self.v = values
        self.currency = values['currency']

    # -- small helpers ---------------------------------------------------------------------------------------
    def cbc(self, parent, tag, text, **attrs):
        if text is None or text is False or text == '':
            return None
        node = etree.SubElement(parent, CBC + tag, {k: str(v) for k, v in attrs.items() if v})
        node.text = str(text)
        return node

    def cac(self, parent, tag):
        return etree.SubElement(parent, CAC + tag)

    def money(self, parent, tag, value, currency=None, digits=2):
        return self.cbc(parent, tag, amount(value, digits), currencyID=currency or self.currency)

    def tax_category(self, parent, tag, category, percent, exemption_code=None, exemption_reason=None):
        node = self.cac(parent, tag)
        self.cbc(node, 'ID', category)
        if percent is not None:
            self.cbc(node, 'Percent', number(percent, 2))
        self.cbc(node, 'TaxExemptionReasonCode', exemption_code)
        self.cbc(node, 'TaxExemptionReason', exemption_reason)
        self.cbc(self.cac(node, 'TaxScheme'), 'ID', 'VAT')
        return node

    def address(self, parent, tag, addr):
        node = self.cac(parent, tag)
        self.cbc(node, 'StreetName', addr.get('street'))
        self.cbc(node, 'AdditionalStreetName', addr.get('street2'))
        self.cbc(node, 'CityName', addr.get('city'))
        self.cbc(node, 'PostalZone', addr.get('zip'))
        self.cbc(node, 'CountrySubentity', addr.get('subentity'))
        self.cbc(self.cac(node, 'Country'), 'IdentificationCode', addr.get('country'))
        return node

    def party(self, parent, tag, data):
        party = self.cac(self.cac(parent, tag), 'Party')
        self.cbc(party, 'EndpointID', data.get('endpoint'), schemeID=data.get('scheme'))
        if data.get('trade_name'):
            self.cbc(self.cac(party, 'PartyName'), 'Name', data['trade_name'])
        self.address(party, 'PostalAddress', data.get('address') or {})
        if data.get('trn'):
            scheme = self.cac(party, 'PartyTaxScheme')
            self.cbc(scheme, 'CompanyID', data['trn'])
            self.cbc(self.cac(scheme, 'TaxScheme'), 'ID', 'VAT')
        legal = self.cac(party, 'PartyLegalEntity')
        self.cbc(legal, 'RegistrationName', data.get('name'))
        legal_id = data.get('legal') or {}
        self.cbc(legal, 'CompanyID', legal_id.get('id'), schemeAgencyID=legal_id.get('type'),
                 schemeAgencyName=legal_id.get('authority'))
        contact = data.get('contact') or {}
        if any(contact.values()):
            node = self.cac(party, 'Contact')
            self.cbc(node, 'Name', contact.get('name'))
            self.cbc(node, 'Telephone', contact.get('phone'))
            self.cbc(node, 'ElectronicMail', contact.get('email'))

    def identified_party(self, parent, tag, identifier):
        """BuyerCustomerParty (FTZ beneficiary, BTAE-01) / SellerSupplierParty (agent principal, BTAE-14)."""
        party = self.cac(self.cac(parent, tag), 'Party')
        self.cbc(self.cac(party, 'PartyIdentification'), 'ID', identifier)

    def period(self, parent, period):
        node = self.cac(parent, 'InvoicePeriod')
        self.cbc(node, 'StartDate', period.get('start'))
        self.cbc(node, 'EndDate', period.get('end'))
        self.cbc(node, 'DescriptionCode', period.get('frequency'))

    # -- document --------------------------------------------------------------------------------------------
    def build(self):
        v = self.v
        is_invoice = v['kind'] == 'invoice'
        root = etree.Element('{%s}%s' % (NS[v['kind']], 'Invoice' if is_invoice else 'CreditNote'),
                             nsmap={None: NS[v['kind']], 'cac': NS['cac'], 'cbc': NS['cbc']})
        self.cbc(root, 'CustomizationID', v['customization_id'])
        self.cbc(root, 'ProfileID', v['profile_id'])
        self.cbc(root, 'ProfileExecutionID', v['transaction_code'])
        self.cbc(root, 'ID', v['number'])
        self.cbc(root, 'UUID', v['uuid'])
        self.cbc(root, 'IssueDate', v['issue_date'])
        if is_invoice:
            self.cbc(root, 'DueDate', v.get('due_date'))
            self.cbc(root, 'InvoiceTypeCode', v['type_code'])
        else:
            self.cbc(root, 'CreditNoteTypeCode', v['type_code'])
        self.cbc(root, 'Note', v.get('note'))
        self.cbc(root, 'DocumentCurrencyCode', self.currency)
        self.cbc(root, 'TaxCurrencyCode', v.get('tax_currency'))
        self.cbc(root, 'BuyerReference', v.get('buyer_reference'))
        if v.get('period'):
            self.period(root, v['period'])
        if not is_invoice and v.get('credit_reason'):
            self.cbc(self.cac(root, 'DiscrepancyResponse'), 'ResponseCode', v['credit_reason'])
        if v.get('order_reference'):
            self.cbc(self.cac(root, 'OrderReference'), 'ID', v['order_reference'])
        for ref in v.get('billing_references') or []:
            doc = self.cac(self.cac(root, 'BillingReference'), 'InvoiceDocumentReference')
            self.cbc(doc, 'ID', ref['id'])
            self.cbc(doc, 'IssueDate', ref.get('date'))
        if v.get('aed_total') is not None:
            # BTAE-20, Invoice total amount with VAT in AED (official "Exports" example)
            doc = self.cac(root, 'AdditionalDocumentReference')
            self.cbc(doc, 'ID', 'AED')
            self.cbc(doc, 'DocumentTypeCode', 'aedtotal-incl-vat')
            self.cbc(doc, 'DocumentDescription', 'AED %s' % amount(v['aed_total']))
        for attachment in v.get('attachments') or []:
            doc = self.cac(root, 'AdditionalDocumentReference')
            self.cbc(doc, 'ID', attachment['id'])
            self.cbc(self.cac(doc, 'Attachment'), 'EmbeddedDocumentBinaryObject', attachment['data'],
                     mimeCode=attachment['mimetype'], filename=attachment['filename'])
        self.party(root, 'AccountingSupplierParty', v['supplier'])
        self.party(root, 'AccountingCustomerParty', v['customer'])
        if v.get('beneficiary_id'):
            self.identified_party(root, 'BuyerCustomerParty', v['beneficiary_id'])
        if v.get('principal_id'):
            self.identified_party(root, 'SellerSupplierParty', v['principal_id'])
        if v.get('delivery'):
            delivery = self.cac(root, 'Delivery')
            self.cbc(delivery, 'ActualDeliveryDate', v['delivery'].get('date'))
            if v['delivery'].get('address'):
                self.address(self.cac(delivery, 'DeliveryLocation'), 'Address', v['delivery']['address'])
            if v['delivery'].get('incoterm'):
                self.cbc(self.cac(delivery, 'DeliveryTerms'), 'ID', v['delivery']['incoterm'], schemeID='Incoterms')
        if v.get('payment_means'):
            means = v['payment_means']
            node = self.cac(root, 'PaymentMeans')
            self.cbc(node, 'PaymentMeansCode', means['code'], name=means.get('name'))
            self.cbc(node, 'PaymentID', means.get('payment_id'))
            if means.get('account'):
                account = self.cac(node, 'PayeeFinancialAccount')
                self.cbc(account, 'ID', means['account'], schemeID=means.get('account_scheme'))
                self.cbc(account, 'Name', means.get('account_name'))
                if means.get('bic'):
                    self.cbc(self.cac(account, 'FinancialInstitutionBranch'), 'ID', means['bic'])
        if v.get('payment_terms'):
            self.cbc(self.cac(root, 'PaymentTerms'), 'Note', v['payment_terms'])
        if v.get('exchange_rate'):
            rate = self.cac(root, 'TaxExchangeRate')
            self.cbc(rate, 'SourceCurrencyCode', self.currency)
            self.cbc(rate, 'TargetCurrencyCode', v['tax_currency'])
            self.cbc(rate, 'CalculationRate', number(v['exchange_rate'], 6))
        tax_total = self.cac(root, 'TaxTotal')
        self.money(tax_total, 'TaxAmount', v['tax_amount'])
        for sub in v['tax_subtotals']:
            node = self.cac(tax_total, 'TaxSubtotal')
            self.money(node, 'TaxableAmount', sub['taxable'])
            self.money(node, 'TaxAmount', sub['tax'])
            self.tax_category(node, 'TaxCategory', sub['category'], sub.get('percent'),
                              sub.get('exemption_code'), sub.get('exemption_reason'))
        if v.get('tax_amount_aed') is not None and abs(float(v['tax_amount_aed']) - float(v['tax_amount'])) > 0.004:
            self.money(self.cac(root, 'TaxTotal'), 'TaxAmount', v['tax_amount_aed'], currency='AED')
        totals = v['totals']
        monetary = self.cac(root, 'LegalMonetaryTotal')
        self.money(monetary, 'LineExtensionAmount', totals['line'])
        self.money(monetary, 'TaxExclusiveAmount', totals['tax_exclusive'])
        self.money(monetary, 'TaxInclusiveAmount', totals['tax_inclusive'])
        if totals.get('prepaid'):
            self.money(monetary, 'PrepaidAmount', totals['prepaid'])
        if totals.get('rounding'):
            self.money(monetary, 'PayableRoundingAmount', totals['rounding'])
        self.money(monetary, 'PayableAmount', totals['payable'])
        for line in v['lines']:
            self.line(root, line, is_invoice)
        return root

    def line(self, root, line, is_invoice):
        node = self.cac(root, 'InvoiceLine' if is_invoice else 'CreditNoteLine')
        self.cbc(node, 'ID', line['id'])
        self.cbc(node, 'InvoicedQuantity' if is_invoice else 'CreditedQuantity', number(line['quantity'], 6),
                 unitCode=line['uom'])
        self.money(node, 'LineExtensionAmount', line['line_amount'])
        if line.get('period'):
            self.period(node, line['period'])
        item = self.cac(node, 'Item')
        self.cbc(item, 'Description', line['description'])
        self.cbc(item, 'Name', line['name'])
        if line.get('seller_code'):
            self.cbc(self.cac(item, 'SellersItemIdentification'), 'ID', line['seller_code'])
        if line.get('standard_id'):
            self.cbc(self.cac(item, 'StandardItemIdentification'), 'ID', line['standard_id'], schemeID='0160')
        if line.get('sac'):
            self.cbc(self.cac(item, 'AdditionalItemIdentification'), 'ID', line['sac'], schemeID='SAC')
        if line.get('nature') or line.get('item_type') or line.get('hs'):
            classification = self.cac(item, 'CommodityClassification')
            self.cbc(classification, 'NatureCode', line.get('nature'))
            self.cbc(classification, 'CommodityCode', line.get('item_type'))
            self.cbc(classification, 'ItemClassificationCode', line.get('hs'), listID='HS')
        self.tax_category(item, 'ClassifiedTaxCategory', line['category'], line.get('percent'),
                          line.get('exemption_code'), line.get('exemption_reason'))
        price = self.cac(node, 'Price')
        self.money(price, 'PriceAmount', line['net_price'], digits=6)
        self.cbc(price, 'BaseQuantity', '1', unitCode=line['uom'])
        allowance = self.cac(price, 'AllowanceCharge')
        self.cbc(allowance, 'ChargeIndicator', 'false')
        self.money(allowance, 'Amount', line['price_discount'], digits=6)
        self.money(allowance, 'BaseAmount', line['gross_price'], digits=6)
        extension = self.cac(node, 'ItemPriceExtension')
        self.money(extension, 'Amount', line['amount_incl'])
        if line.get('line_tax') is not None:
            self.money(self.cac(extension, 'TaxTotal'), 'TaxAmount', line['line_tax'])


def render(values):
    """values (dict) -> UTF-8 XML bytes."""
    root = _Writer(values).build()
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', pretty_print=True)
