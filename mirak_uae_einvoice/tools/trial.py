# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Trial (demo) build: the whole flow can be tested with the simulation provider, but only a limited number of
documents can be sent and the REST connector (a real Accredited Service Provider) is reserved to the full version.
A QR code points to WhatsApp to buy the full version."""
from markupsafe import Markup

from odoo import _
from odoo.exceptions import UserError

TRIAL_MAX_DOCUMENTS = 15
WHATSAPP_NUMBER = '97466999180'
PURCHASE_TEXT = ('Hello! I want to buy the full version of the Mirak UAE E-Invoicing module (PINT-AE) for Odoo 19.')
QR_IMAGE_PATH = '/mirak_uae_einvoice/static/img/whatsapp_qr.png'


def purchase_url():
    from urllib.parse import quote
    return 'https://wa.me/%s?text=%s' % (WHATSAPP_NUMBER, quote(PURCHASE_TEXT))


def documents_used(env, company_id=None):
    """Number of e-invoices already handed to a provider (sent or accepted)."""
    domain = [('mirak_ae_state', 'in', ('sent', 'accepted'))]
    if company_id:
        domain.append(('company_id', '=', company_id))
    return env['account.move'].sudo().search_count(domain)


def remaining(env, company_id=None):
    return max(0, TRIAL_MAX_DOCUMENTS - documents_used(env, company_id))


def limit_reached(env, company_id=None):
    return documents_used(env, company_id) >= TRIAL_MAX_DOCUMENTS


def trial_banner(env, company_id=None):
    """HTML banner shown in the dashboard and the settings: trial state + QR code to buy the full version."""
    used = documents_used(env, company_id)
    left = max(0, TRIAL_MAX_DOCUMENTS - used)
    return Markup(
        '<div class="o_mirak_ae_trial alert alert-warning d-flex align-items-center gap-3 flex-wrap mb-3">'
        '<img src="%s" alt="WhatsApp QR" width="110" height="110" class="rounded border bg-white p-1"/>'
        '<div class="flex-grow-1">'
        '<h4 class="alert-heading mb-1">%s · <span dir="rtl">%s</span></h4>'
        '<div>%s: <b>%s / %s</b> — %s: <b class="%s">%s</b></div>'
        '<div dir="rtl" class="mt-1">%s: <b>%s / %s</b> — %s: <b>%s</b></div>'
        '<div class="mt-2">%s <a href="%s" target="_blank" class="alert-link fw-bold">%s</a> (%s)</div>'
        '</div></div>') % (
        QR_IMAGE_PATH,
        _('Trial version'), _('نسخة تجريبية'),
        _('E-invoices sent'), used, TRIAL_MAX_DOCUMENTS, _('remaining'), 'text-success' if left else 'text-danger',
        left,
        _('فواتير إلكترونية مُرسلة'), used, TRIAL_MAX_DOCUMENTS, _('المتبقي'), left,
        _('To send without limit and connect your real service provider (ASP), buy the full version:'),
        purchase_url(), _('chat with us on WhatsApp'), '+' + WHATSAPP_NUMBER,
    )


def check_document_limit(env, company_id=None):
    """Raise a UserError with the QR banner when the trial limit is reached."""
    if limit_reached(env, company_id):
        raise UserError(Markup(
            '<div class="o_mirak_ae_trial d-flex align-items-center gap-3">'
            '<img src="%s" alt="WhatsApp QR" width="130" height="130" class="rounded border"/>'
            '<div>%s<br/><b>%s / %s</b><br/>%s<br/>%s</div></div>') % (
            QR_IMAGE_PATH,
            _('The trial version allows %(max)s e-invoices: the limit is reached.', max=TRIAL_MAX_DOCUMENTS),
            documents_used(env, company_id), TRIAL_MAX_DOCUMENTS,
            _('Scan the QR code or click to buy the full version:'),
            '<a href="%s" target="_blank">%s</a>' % (purchase_url(), _('WhatsApp +%s', WHATSAPP_NUMBER)),
        ))
