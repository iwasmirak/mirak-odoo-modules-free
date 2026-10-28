# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Pre-validation, sending, retries and status follow-up of PINT-AE documents."""
import logging
import re

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..connectors import ConnectorResult
from ..tools import pint_ae, schemas, schematron, trial
from ..tools.messages import issue

_logger = logging.getLogger(__name__)

ISO_COUNTRY = re.compile(r'[A-Z]{2}')
EMIRATE_CODES = {code for code, _label in pint_ae.EMIRATES}


class AccountMove(models.Model):
    _inherit = 'account.move'

    mirak_ae_log_ids = fields.One2many('mirak.ae.log', 'move_id', string='E-Invoice Log')
    mirak_ae_issues_html = fields.Html(compute='_compute_mirak_ae_issues_html', sanitize=False)

    @api.depends('mirak_ae_issues')
    def _compute_mirak_ae_issues_html(self):
        badge = {'error': 'text-bg-danger', 'warning': 'text-bg-warning'}
        for move in self:
            issues = move.mirak_ae_issues or []
            if not issues:
                move.mirak_ae_issues_html = False
                continue
            rows = Markup()
            for item in issues:
                fix = item.get('fix') or {}
                link = Markup('')
                if fix.get('model') and fix.get('id'):
                    link = Markup('<a href="/odoo/%s/%s" target="_blank" class="btn btn-sm btn-link">%s</a>') % (
                        fix['model'], int(fix['id']), _('Fix'))
                rows += Markup(
                    '<tr><td><span class="badge %s">%s</span></td><td class="text-nowrap"><code>%s</code></td>'
                    '<td>%s</td><td dir="rtl" class="text-end">%s</td><td>%s</td></tr>') % (
                    badge.get(item.get('level'), 'text-bg-secondary'),
                    _('Error') if item.get('level') == 'error' else _('Warning'),
                    item.get('rule') or '-', item.get('en'), item.get('ar'), link)
            move.mirak_ae_issues_html = Markup(
                '<table class="table table-sm align-middle"><thead><tr><th/><th>%s</th><th>English</th>'
                '<th class="text-end">العربية</th><th/></tr></thead><tbody>%s</tbody></table>') % (_('Rule'), rows)

    # ------------------------------------------------------------------------------------------------------------
    # Pre-validation
    # ------------------------------------------------------------------------------------------------------------
    @api.model
    def _mirak_ae_fix(self, record, view=None):
        return {'model': record._name, 'id': record.id, 'view': view or ''} if record else {}

    def _mirak_ae_check_company(self):
        company = self.company_id
        fix = self._mirak_ae_fix(company)
        name = company.name
        issues = []
        if not pint_ae.is_valid_trn(company.mirak_ae_trn):
            issues.append(issue('company_trn', fix=fix, name=name))
        if not pint_ae.is_valid_tin(company.mirak_ae_endpoint_id):
            issues.append(issue('company_endpoint', fix=fix, name=name))
        if not company.mirak_ae_legal_id:
            issues.append(issue('company_legal_id', fix=fix, name=name))
        elif not company.mirak_ae_legal_id_type:
            issues.append(issue('company_legal_type', fix=fix, name=name))
        elif company.mirak_ae_legal_id_type == 'TL' and not company.mirak_ae_legal_authority:
            issues.append(issue('company_authority', fix=fix, name=name))
        elif company.mirak_ae_legal_id_type == 'PAS' and \
                not ISO_COUNTRY.fullmatch(company.mirak_ae_legal_authority or ''):
            issues.append(issue('company_passport', fix=fix, name=name))
        address = self._mirak_ae_address(company.partner_id, company.mirak_ae_emirate)
        if not (address['street'] and address['city']):
            issues.append(issue('company_address', fix=self._mirak_ae_fix(company.partner_id), name=name))
        if address['subentity'] not in EMIRATE_CODES:
            issues.append(issue('company_emirate', fix=fix, name=name))
        return issues

    def _mirak_ae_check_customer(self, categories, type_code):
        partner = self.partner_id.commercial_partner_id
        fix = self._mirak_ae_fix(partner)
        name = partner.display_name
        customer = self._mirak_ae_customer_values()
        issues = []
        address = customer['address']
        if not (address['street'] and address['city'] and address['subentity'] and address['country']):
            issues.append(issue('buyer_address', fix=fix, name=name))
        if address['country'] == 'AE' and address['subentity'] and address['subentity'] not in EMIRATE_CODES:
            issues.append(issue('buyer_emirate', fix=fix, name=name))
        if partner.mirak_ae_trn and not pint_ae.is_valid_trn(partner.mirak_ae_trn):
            issues.append(issue('buyer_trn', fix=fix, name=name))
        if customer['scheme'] == pint_ae.UAE_EAS and not re.match(r'[19]', customer['endpoint'] or '') \
                and not partner.mirak_ae_trn:
            issues.append(issue('buyer_trn_needed', fix=fix, name=name, endpoint=customer['endpoint']))
        if 'AE' in categories and not partner.mirak_ae_trn:
            issues.append(issue('buyer_trn_reverse_charge', fix=fix, name=name))
        if partner.mirak_ae_legal_id:
            if not partner.mirak_ae_legal_id_type:
                issues.append(issue('buyer_legal_type', fix=fix, name=name))
            elif partner.mirak_ae_legal_id_type == 'TL' and not partner.mirak_ae_legal_authority:
                issues.append(issue('buyer_authority', fix=fix, name=name))
            elif partner.mirak_ae_legal_id_type == 'PAS' and \
                    not ISO_COUNTRY.fullmatch(partner.mirak_ae_legal_authority or ''):
                issues.append(issue('buyer_passport', fix=fix, name=name))
        elif type_code in ('480', '81'):
            issues.append(issue('buyer_legal_oos', fix=fix, name=name))
        if customer['endpoint'] == pint_ae.ENDPOINT_BUYER_NOT_ONBOARDED and partner.mirak_ae_scope in ('b2b', 'b2g'):
            issues.append(issue('buyer_not_on_peppol', level='warning', fix=fix, name=name))
        return issues

    def _mirak_ae_check_lines(self):
        issues = []
        for index, line in enumerate(self._mirak_ae_invoice_lines(), start=1):
            label = '%s (%s)' % (index, (line.product_id.display_name or line.name or '')[:40])
            vat_taxes = line.tax_ids.filtered('mirak_ae_category')
            product = line.product_id.product_tmpl_id
            product_fix = self._mirak_ae_fix(product)
            if not vat_taxes:
                fix = self._mirak_ae_fix(line.tax_ids[:1]) if line.tax_ids else {'action': 'taxes'}
                issues.append(issue('line_no_tax', fix=fix, line=label))
                continue
            if len(vat_taxes) > 1:
                issues.append(issue('line_many_taxes', line=label))
            tax = vat_taxes[0]
            if tax.mirak_ae_category == 'S' and tax.amount != 5:
                issues.append(issue('line_rate', fix=self._mirak_ae_fix(tax), line=label,
                                    rate=('%g' % tax.amount)))
            if tax.mirak_ae_category == 'E' and not tax.mirak_ae_exemption_code:
                issues.append(issue('line_exemption', fix=self._mirak_ae_fix(tax), line=label, tax=tax.name))
            if tax.mirak_ae_category == 'AE':
                if not line.product_id.barcode:
                    issues.append(issue('line_rc_barcode', fix=product_fix, line=label))
                if not product.mirak_ae_goods_type:
                    issues.append(issue('line_rc_goods', fix=product_fix, line=label))
            if product.mirak_ae_item_type in ('G', 'B') and not product.mirak_ae_hs_code:
                issues.append(issue('line_hs', fix=product_fix, line=label))
            if product.mirak_ae_item_type in ('S', 'B') and not product.mirak_ae_sac_code:
                issues.append(issue('line_sac', fix=product_fix, line=label))
            if not (line.name or line.product_id.display_name or '').strip():
                issues.append(issue('line_description', line=label))
        return issues

    def _mirak_ae_check_document(self, values):
        issues = []
        fix = self._mirak_ae_fix(self)
        is_invoice = self.move_type == 'out_invoice'
        if self.state != 'posted':
            issues.append(issue('not_posted', fix=fix))
        if not values['lines']:
            issues.append(issue('no_lines', fix=fix))
        if not is_invoice:
            if not self.mirak_ae_credit_reason:
                issues.append(issue('credit_reason', fix=fix))
            elif self.mirak_ae_credit_reason != 'VD' and not self.reversed_entry_id:
                issues.append(issue('credit_origin', fix=fix))
        elif values['totals']['payable'] > 0 and not values.get('due_date') and not self.mirak_ae_tt_deemed_supply:
            issues.append(issue('due_date', fix=fix))
        means = values.get('payment_means') or {}
        if means.get('code') == '30' and not means.get('account'):
            issues.append(issue('payment_account', fix=fix))
        if self.mirak_ae_tt_free_trade_zone and not pint_ae.is_valid_trn(self.mirak_ae_beneficiary_trn):
            issues.append(issue('beneficiary', fix=fix))
        if self.mirak_ae_tt_agent_billing and (not pint_ae.is_valid_trn(self.mirak_ae_principal_trn) or
                                               self.mirak_ae_principal_trn == self.company_id.mirak_ae_trn):
            issues.append(issue('principal', fix=fix))
        if self.mirak_ae_tt_summary_invoice and not (self.mirak_ae_period_start or self.mirak_ae_period_end):
            issues.append(issue('summary_period', fix=fix))
        if self.mirak_ae_tt_margin_scheme:
            issues.append(issue('margin_scheme', fix=fix))
        delivery = values.get('delivery')
        if delivery:
            address = delivery.get('address') or {}
            if not (address.get('street') and address.get('city') and address.get('subentity')):
                partner = self.partner_shipping_id or self.partner_id
                issues.append(issue('delivery_address', fix=self._mirak_ae_fix(partner)))
        if self.currency_id.name != 'AED' and not values.get('exchange_rate'):
            issues.append(issue('company_currency', fix=fix, name=self.company_id.name,
                                currency=self.currency_id.name))
        return issues

    def _mirak_ae_prevalidate(self, xml_content=None):
        """All checks of one document -> list of issues (dicts with en / ar texts, level, rule, fix)."""
        self.ensure_one()
        try:
            values = self._mirak_ae_prepare_values()
        except Exception as error:      # noqa: BLE001 - shown to the user, never hidden
            _logger.exception('PINT-AE values of %s', self.display_name)
            return [issue('xsd', detail=str(error))]
        categories = {line['category'] for line in values['lines']}
        issues = (self._mirak_ae_check_company()
                  + self._mirak_ae_check_customer(categories, values['type_code'])
                  + self._mirak_ae_check_lines()
                  + self._mirak_ae_check_document(values))
        if xml_content and not any(i['level'] == 'error' for i in issues):
            issues += [issue('xsd', detail=error) for error in schemas.validate_xsd(xml_content)[:10]]
            results = schematron.validate(xml_content)
            for rule, flag, text in results or []:
                issues.append(issue('schematron', level='warning' if flag == 'warning' else 'error',
                                    rule=rule, detail=text))
        return issues

    @api.model
    def _mirak_ae_blocking(self, issues):
        return [i for i in issues or [] if i['level'] == 'error']

    def action_mirak_ae_prevalidate(self):
        """Button: check the document and show the result in the pre-validation tab."""
        for move in self:
            xml_content = move._mirak_ae_build_xml() if move.state == 'posted' else None
            issues = move._mirak_ae_prevalidate(xml_content)
            move.mirak_ae_issues = issues
            if move.state == 'posted' and not move._mirak_ae_blocking(issues):
                move._mirak_ae_attach_xml(xml_content)
                if move.mirak_ae_state in (False, 'draft', 'error'):
                    move.mirak_ae_state = 'ready'
        if len(self) == 1:
            blocking = self._mirak_ae_blocking(self.mirak_ae_issues)
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'type': 'danger' if blocking else 'success',
                'title': _('Pre-validation'),
                'message': _('%s problem(s) to fix before sending.', len(blocking)) if blocking else
                _('The e-invoice is valid and ready to send.') + ('' if schematron.available() else ' ' + _(
                    '(Official Schematron not run: install the saxonche Python package to enable it.)')),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                'sticky': bool(blocking),
            }}
        return True

    # ------------------------------------------------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------------------------------------------------
    def _mirak_ae_log(self, operation, result, provider=None, attempt=0):
        self.ensure_one()
        status = getattr(result, 'status', result if isinstance(result, str) else 'unknown')
        self.env['mirak.ae.log'].sudo().create({
            'move_id': self.id,
            'provider_id': provider.id if provider else False,
            'operation': operation,
            'result': status if status in dict(self.env['mirak.ae.log']._fields['result'].selection) else 'unknown',
            'reference': getattr(result, 'reference', '') or False,
            'message': getattr(result, 'message', '') or False,
            'request': getattr(result, 'request', '') or False,
            'response': getattr(result, 'response', '') or False,
            'attempt': attempt,
        })

    def _mirak_ae_post_message(self, title, text, css='info'):
        body = Markup('<div class="alert alert-%s mb-0"><b>%s</b><br/>%s</div>') % (
            css, escape(title), escape(text or ''))
        self.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')

    def _mirak_ae_send_one(self):
        """Validate then hand one document to the provider. Never raises: the outcome is stored on the move."""
        self.ensure_one()
        company = self.company_id
        provider = company.mirak_ae_provider_id
        xml_content = self._mirak_ae_build_xml()
        issues = self._mirak_ae_prevalidate(xml_content)
        if not provider:
            issues.append(issue('no_provider', fix={'action': 'settings'}))
        self.mirak_ae_issues = issues
        blocking = self._mirak_ae_blocking(issues)
        if blocking:
            self.mirak_ae_state = 'draft'    # needs a correction by a user: not retried automatically
            message = '\n'.join('- [%s] %s / %s' % (i['rule'] or '-', i['en'], i['ar']) for i in blocking)
            self.mirak_ae_message = message
            self._mirak_ae_log('validate', 'blocked', provider)
            return False
        self._mirak_ae_attach_xml(xml_content)
        attempt = self.mirak_ae_attempts + 1
        document = {
            'number': self.name, 'uuid': self.mirak_ae_uuid,
            'type': 'invoice' if self.move_type == 'out_invoice' else 'credit_note',
            'sender': '%s:%s' % (pint_ae.UAE_EAS, company.mirak_ae_endpoint_id),
            'receiver': '%(scheme)s:%(endpoint)s' % self._mirak_ae_customer_values(),
            'filename': self._mirak_ae_filename(),
        }
        try:
            result = provider._get_connector().send(xml_content, document)
        except Exception as error:      # noqa: BLE001 - network or provider failure, retried by the cron
            _logger.warning('PINT-AE sending of %s failed: %s', self.display_name, type(error).__name__)
            result = ConnectorResult(ok=False, status='error', message=str(error))
        state = result.status if result.status in ('sent', 'accepted', 'rejected') else 'error'
        self.write({
            'mirak_ae_state': state,
            'mirak_ae_attempts': attempt,
            'mirak_ae_last_attempt': fields.Datetime.now(),
            'mirak_ae_reference': result.reference or self.mirak_ae_reference,
            'mirak_ae_message': result.message,
        })
        self._mirak_ae_log('send', result, provider, attempt)
        titles = {'sent': _('E-invoice sent to the service provider'), 'accepted': _('E-invoice accepted'),
                  'rejected': _('E-invoice rejected'), 'error': _('E-invoice sending failed (attempt %s)', attempt)}
        css = {'sent': 'info', 'accepted': 'success', 'rejected': 'danger', 'error': 'warning'}[state]
        detail = result.message or ''
        if result.reference:
            detail = '%s\n%s: %s' % (detail, _('Reference'), result.reference)
        self._mirak_ae_post_message(titles[state], detail, css)
        return state in ('sent', 'accepted')

    def _mirak_ae_check_sendable(self):
        for move in self:
            if not move.mirak_ae_applicable:
                raise UserError(_('%s is not a customer invoice or credit note of a company with a TRN.',
                                  move.display_name))
            if move.state != 'posted':
                raise UserError(_('Post %s before sending its e-invoice.', move.display_name))
            if move.mirak_ae_state in ('sent', 'accepted'):
                raise UserError(_('%s was already sent.', move.display_name))

    def action_mirak_ae_send(self):
        """Button: send now. Shows the problems found by the pre-validation."""
        self._mirak_ae_check_sendable()
        for company in self.company_id:
            trial.check_document_limit(self.env, company.id)
        done = self.browse()
        for move in self:
            if move._mirak_ae_send_one():
                done |= move
        failed = self - done
        if len(self) == 1 and failed and self._mirak_ae_blocking(self.mirak_ae_issues):
            lines = ['• [%s] %s\n   %s' % (i['rule'] or '-', i['en'], i['ar'])
                     for i in self._mirak_ae_blocking(self.mirak_ae_issues)]
            raise UserError(_('The e-invoice cannot be sent:\n\n%s', '\n'.join(lines)))
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success' if not failed else 'warning',
            'title': _('UAE E-Invoicing'),
            'message': _('%(done)s sent, %(failed)s not sent (see the E-Invoice tab).', done=len(done),
                         failed=len(failed)),
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
        }}

    def action_mirak_ae_check_status(self):
        for move in self.filtered(lambda m: m.mirak_ae_state == 'sent' and m.mirak_ae_reference):
            move._mirak_ae_poll_status()
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def _mirak_ae_poll_status(self):
        self.ensure_one()
        provider = self.company_id.mirak_ae_provider_id
        if not provider:
            return
        try:
            result = provider._get_connector().get_status(self.mirak_ae_reference)
        except Exception as error:      # noqa: BLE001
            _logger.warning('PINT-AE status of %s failed: %s', self.display_name, type(error).__name__)
            return
        self._mirak_ae_log('status', result, provider)
        if result.ok and result.status in ('accepted', 'rejected') and result.status != self.mirak_ae_state:
            self.write({'mirak_ae_state': result.status, 'mirak_ae_message': result.message})
            if result.status == 'accepted':
                self._mirak_ae_post_message(_('E-invoice accepted'), result.message, 'success')
            else:
                self._mirak_ae_post_message(_('E-invoice rejected'), result.message, 'danger')

    def action_mirak_ae_reset(self):
        """After a rejection or an error: back to "Not sent" so that the document can be corrected and resent."""
        for move in self.filtered(lambda m: m.mirak_ae_state in ('rejected', 'error')):
            move.write({'mirak_ae_state': 'draft', 'mirak_ae_attempts': 0, 'mirak_ae_reference': False})
            move._mirak_ae_post_message(_('E-invoice reset'), _('The document can be corrected and sent again.'))
        return True

    # ------------------------------------------------------------------------------------------------------------
    # Posting, reset to draft, cron
    # ------------------------------------------------------------------------------------------------------------
    def _post(self, soft=True):
        posted = super()._post(soft)
        to_queue = posted.filtered(lambda m: m.mirak_ae_applicable)
        to_queue.filtered(lambda m: not m.mirak_ae_state).mirak_ae_state = 'draft'
        auto = to_queue.filtered(lambda m: m.company_id.mirak_ae_auto_send and m.mirak_ae_state == 'draft')
        if auto:
            auto.mirak_ae_state = 'ready'
            cron = self.env.ref('mirak_uae_einvoice.ir_cron_mirak_ae_process', raise_if_not_found=False)
            if cron:
                cron._trigger()
        return posted

    def button_draft(self):
        for move in self:
            if move.mirak_ae_state in ('sent', 'accepted'):
                raise UserError(_('%s was sent as an e-invoice: issue a credit note instead of changing it.',
                                  move.display_name))
        return super().button_draft()

    @api.model
    def _cron_mirak_ae_process(self, batch_size=50, commit=False):
        """Send the documents waiting (ready / error below the retry limit) and follow the sent ones."""
        moves = self.search([('mirak_ae_state', 'in', ('ready', 'error')), ('state', '=', 'posted')],
                            order='id', limit=batch_size * 4)
        moves = moves.filtered(lambda m: m.mirak_ae_state == 'ready' or
                               m.mirak_ae_attempts < (m.company_id.mirak_ae_max_attempts or 5))[:batch_size]
        # trial build: never raise inside the cron, only skip the companies at the limit
        moves = moves.filtered(lambda m: not trial.limit_reached(self.env, m.company_id.id))
        for index, move in enumerate(moves, start=1):
            move._mirak_ae_send_one()
            if commit:      # from the scheduled action: keep each result, even if a later document fails
                self.env['ir.cron']._commit_progress(1, remaining=len(moves) - index)
        for move in self.search([('mirak_ae_state', '=', 'sent'), ('mirak_ae_reference', '!=', False)],
                                limit=batch_size):
            move._mirak_ae_poll_status()
        return True
