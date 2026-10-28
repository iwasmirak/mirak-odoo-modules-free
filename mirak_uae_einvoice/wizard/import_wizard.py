# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Create vendor bills / refunds from PINT-AE (UBL 2.1) e-invoices received from suppliers."""
import base64
import logging

from lxml import etree
from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..tools import pint_ae, trial
from ..tools.ubl_writer import NS

_logger = logging.getLogger(__name__)
X = {'cac': NS['cac'], 'cbc': NS['cbc']}


def _text(node, path):
    found = node.xpath(path, namespaces=X) if node is not None else []
    if not found:
        return ''
    value = found[0]
    return (value if isinstance(value, str) else value.text or '').strip()


def _float(node, path):
    try:
        return float(_text(node, path) or 0.0)
    except ValueError:
        return 0.0


class MirakAeImportWizard(models.TransientModel):
    _name = 'mirak.ae.import.wizard'
    _description = 'Import Vendor E-Invoices (PINT-AE)'

    attachment_ids = fields.Many2many('ir.attachment', string='XML Files', required=True)
    journal_id = fields.Many2one('account.journal', string='Purchase Journal', required=True,
                                 domain="[('type', '=', 'purchase'), ('company_id', '=', company_id)]",
                                 default=lambda self: self.env['account.journal'].search(
                                     [('type', '=', 'purchase'), ('company_id', '=', self.env.company.id)], limit=1))
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    create_partners = fields.Boolean(string='Create Unknown Suppliers', default=True)
    result = fields.Text(readonly=True)
    move_ids = fields.Many2many('account.move', string='Created Bills', readonly=True)

    # ------------------------------------------------------------------------------------------------------------
    def action_import(self):
        self.ensure_one()
        trial.check_document_limit(self.env, self.company_id.id)
        moves = self.env['account.move']
        report = []
        for attachment in self.attachment_ids:
            try:
                with self.env.cr.savepoint():
                    move, notes = self._import_one(bytes(attachment.raw), attachment.name)
                moves |= move
                report.append('%s: %s %s' % (attachment.name, move.display_name, ' '.join(notes)))
            except UserError as error:
                report.append('%s: %s' % (attachment.name, error.args[0]))
        self.write({'result': '\n'.join(report), 'move_ids': [Command.set(moves.ids)]})
        if moves and len(moves) == len(self.attachment_ids):
            action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_in_invoice_type')
            action['domain'] = [('id', 'in', moves.ids)]
            if len(moves) == 1:
                action.update({'res_id': moves.id, 'view_mode': 'form', 'views': [(False, 'form')]})
            return action
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'new'}

    # ------------------------------------------------------------------------------------------------------------
    @api.model
    def _parse(self, content):
        try:
            root = etree.fromstring(content)
        except etree.XMLSyntaxError as error:
            raise UserError(_('Not a valid XML file (%s).', error)) from error
        kind = etree.QName(root).localname
        if kind not in ('Invoice', 'CreditNote'):
            raise UserError(_('Not a UBL invoice or credit note.'))
        customization = _text(root, 'cbc:CustomizationID')
        if not customization.startswith(pint_ae.CUSTOMIZATION_ID.split('@')[0]):
            raise UserError(_('Not a Peppol PINT document (CustomizationID "%s").', customization))
        return root, kind

    def _find_supplier(self, party):
        Partner = self.env['res.partner']
        trn = pint_ae.normalize_trn(_text(party, 'cac:PartyTaxScheme/cbc:CompanyID'))
        endpoint = _text(party, 'cbc:EndpointID')
        name = _text(party, 'cac:PartyLegalEntity/cbc:RegistrationName') or _text(party, 'cac:PartyName/cbc:Name')
        own_partners = self.env['res.company'].sudo().search([]).partner_id
        domain_company = ['|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
                          ('id', 'not in', own_partners.ids)]   # never your own company as supplier
        partner = Partner.browse()
        if trn:
            partner = Partner.search(domain_company + ['|', ('mirak_ae_trn', '=', trn), ('vat', '=', trn)], limit=1)
        if not partner and endpoint and not trn:
            partner = Partner.search(domain_company + [('mirak_ae_endpoint_id', '=', endpoint)], limit=1)
        if not partner and name and not trn and not endpoint:
            # the name is only a last resort: with a TRN, a different TRN means a different supplier
            partner = Partner.search(domain_company + [('name', '=ilike', name), ('is_company', '=', True)], limit=1)
        if partner:
            return partner.commercial_partner_id, False
        if not self.create_partners:
            raise UserError(_('Unknown supplier %(name)s (TRN %(trn)s).', name=name, trn=trn or '-'))
        address = party.xpath('cac:PostalAddress', namespaces=X)
        address = address[0] if address else None
        country = self.env['res.country'].search([('code', '=', _text(address, 'cac:Country/cbc:IdentificationCode'))],
                                                 limit=1)
        subentity = _text(address, 'cbc:CountrySubentity')
        legal = party.xpath('cac:PartyLegalEntity/cbc:CompanyID', namespaces=X)
        values = {
            'name': name or trn or endpoint, 'is_company': True, 'company_id': False,
            'street': _text(address, 'cbc:StreetName'), 'street2': _text(address, 'cbc:AdditionalStreetName'),
            'city': _text(address, 'cbc:CityName'), 'zip': _text(address, 'cbc:PostalZone'),
            'country_id': country.id,
            'mirak_ae_emirate': subentity if subentity in dict(pint_ae.EMIRATES) else False,
            'mirak_ae_trn': trn if pint_ae.is_valid_trn(trn) else False,
            'vat': trn or False,
            'mirak_ae_endpoint_id': endpoint or False,
            'email': _text(party, 'cac:Contact/cbc:ElectronicMail') or False,
            'phone': _text(party, 'cac:Contact/cbc:Telephone') or False,
        }
        if legal:
            values.update({'mirak_ae_legal_id': (legal[0].text or '').strip(),
                           'mirak_ae_legal_id_type': legal[0].get('schemeAgencyID')
                           if legal[0].get('schemeAgencyID') in dict(pint_ae.LEGAL_ID_TYPES) else False,
                           'mirak_ae_legal_authority': legal[0].get('schemeAgencyName')})
        return Partner.create(values), True

    def _find_product(self, item):
        Product = self.env['product.product']
        domain_company = ['|', ('company_id', '=', False), ('company_id', '=', self.company_id.id)]
        for path, field in (('cac:SellersItemIdentification/cbc:ID', 'default_code'),
                            ('cac:StandardItemIdentification/cbc:ID', 'barcode'),
                            ('cac:BuyersItemIdentification/cbc:ID', 'default_code')):
            code = _text(item, path)
            if code:
                product = Product.search(domain_company + [(field, '=', code)], limit=1)
                if product:
                    return product
        name = _text(item, 'cbc:Name')
        if name:
            return Product.search(domain_company + [('name', '=ilike', name)], limit=1)
        return Product

    def _find_tax(self, category, percent):
        if not category:
            return self.env['account.tax']
        domain = [('type_tax_use', '=', 'purchase'), ('mirak_ae_category', '=', category),
                  ('company_id', '=', self.company_id.id)]
        taxes = self.env['account.tax'].search(domain)
        if category in ('S', 'AE', 'Z') and taxes:
            same_rate = taxes.filtered(lambda t: float_compare(t.amount, percent or 0.0, 2) == 0)
            # reverse charge taxes keep the 5% rate on purchases
            taxes = same_rate or taxes
        return taxes[:1]

    def _import_one(self, content, filename):
        root, kind = self._parse(content)
        notes = []
        uuid = _text(root, 'cbc:UUID')
        move_type = 'in_invoice' if kind == 'Invoice' else 'in_refund'
        if uuid and self.env['account.move'].search_count([('mirak_ae_uuid', '=', uuid),
                                                          ('move_type', 'in', ('in_invoice', 'in_refund')),
                                                          ('company_id', '=', self.company_id.id)]):
            raise UserError(_('Already imported (UUID %s).', uuid))
        supplier_node = root.xpath('cac:AccountingSupplierParty/cac:Party', namespaces=X)
        if not supplier_node:
            raise UserError(_('The document has no supplier.'))
        partner, created = self._find_supplier(supplier_node[0])
        if created:
            notes.append(_('(new supplier %s)', partner.name))
        buyer_trn = pint_ae.normalize_trn(_text(root, 'cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/'
                                                      'cbc:CompanyID'))
        if buyer_trn and self.company_id.mirak_ae_trn and buyer_trn != self.company_id.mirak_ae_trn:
            notes.append(_('(warning: addressed to TRN %s, not to your company)', buyer_trn))
        currency = self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', _text(root, 'cbc:DocumentCurrencyCode'))], limit=1)
        if not currency:
            raise UserError(_('Unknown currency %s.', _text(root, 'cbc:DocumentCurrencyCode')))
        if not currency.active:
            currency.active = True

        lines = []
        line_tag = 'cac:InvoiceLine' if kind == 'Invoice' else 'cac:CreditNoteLine'
        qty_tag = 'cbc:InvoicedQuantity' if kind == 'Invoice' else 'cbc:CreditedQuantity'
        missing_products = 0
        for line in root.xpath(line_tag, namespaces=X):
            item = line.xpath('cac:Item', namespaces=X)[0]
            product = self._find_product(item)
            missing_products += not product
            quantity = _float(line, qty_tag) or 1.0
            subtotal = _float(line, 'cbc:LineExtensionAmount')
            category = _text(item, 'cac:ClassifiedTaxCategory/cbc:ID')
            percent = _float(item, 'cac:ClassifiedTaxCategory/cbc:Percent')
            tax = self._find_tax(category, percent)
            description = _text(item, 'cbc:Description') or _text(item, 'cbc:Name')
            vals = {
                'name': description,
                'quantity': quantity,
                'price_unit': subtotal / quantity,
                'tax_ids': [Command.set(tax.ids)],
            }
            if product:
                vals['product_id'] = product.id
            lines.append(Command.create(vals))
        if not lines:
            raise UserError(_('The document has no line.'))
        # document level allowances (negative) and charges (positive), on their own lines
        for node in root.xpath('cac:AllowanceCharge', namespaces=X):
            is_charge = _text(node, 'cbc:ChargeIndicator').lower() == 'true'
            amount = _float(node, 'cbc:Amount')
            tax = self._find_tax(_text(node, 'cac:TaxCategory/cbc:ID'), _float(node, 'cac:TaxCategory/cbc:Percent'))
            reason = _text(node, 'cbc:AllowanceChargeReason') or (_('Charge') if is_charge else _('Allowance'))
            lines.append(Command.create({
                'name': reason, 'quantity': 1.0, 'price_unit': amount if is_charge else -amount,
                'tax_ids': [Command.set(tax.ids)],
            }))

        values = {
            'move_type': move_type,
            'company_id': self.company_id.id,
            'journal_id': self.journal_id.id,
            'partner_id': partner.id,
            'currency_id': currency.id,
            'ref': _text(root, 'cbc:ID'),
            'invoice_date': _text(root, 'cbc:IssueDate') or False,
            'mirak_ae_uuid': uuid or False,
            'invoice_line_ids': lines,
        }
        due = _text(root, 'cbc:DueDate') or _text(root, 'cac:PaymentMeans/cbc:PaymentDueDate')
        if due:
            values['invoice_date_due'] = due
        if kind == 'CreditNote':
            origin = _text(root, 'cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID')
            reason = _text(root, 'cac:DiscrepancyResponse/cbc:ResponseCode')
            if reason in dict(pint_ae.CREDIT_REASONS):
                values['mirak_ae_credit_reason'] = reason
            if origin:
                original = self.env['account.move'].search([('move_type', '=', 'in_invoice'), ('ref', '=', origin),
                                                            ('partner_id', '=', partner.id)], limit=1)
                values['reversed_entry_id'] = original.id or False
        move = self.env['account.move'].with_company(self.company_id).create(values)
        attachment = self.env['ir.attachment'].create({
            'name': filename or '%s.xml' % (values['ref'] or 'einvoice'),
            'raw': content, 'mimetype': 'application/xml', 'res_model': 'account.move', 'res_id': move.id,
        })
        # compared with the total with VAT (the payable amount may include a rounding or a prepaid amount)
        payable = _float(root, 'cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount') or (
            _float(root, 'cac:LegalMonetaryTotal/cbc:PayableAmount') +
            _float(root, 'cac:LegalMonetaryTotal/cbc:PrepaidAmount'))
        checks = [_('Imported from the PINT-AE e-invoice %s.', escape(values['ref'] or ''))]
        if missing_products:
            checks.append(_('%s line(s) without a matching product.', missing_products))
        if currency.compare_amounts(move.amount_total, payable):
            checks.append(_('Total in the e-invoice: %(file)s, total of the bill: %(bill)s. Check the taxes.',
                            file=payable, bill=move.amount_total))
            notes.append(_('(totals differ, check it)'))
        move.message_post(body=Markup('<br/>').join(escape(c) for c in checks), attachment_ids=attachment.ids)
        return move, notes
