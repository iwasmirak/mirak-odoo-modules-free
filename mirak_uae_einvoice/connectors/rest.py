# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Generic REST connector: works with any provider whose API takes the UBL XML in a POST and answers JSON.

The paths, the authentication header and the JSON keys are set on the provider record, so most providers can be
used without code. Providers with an unusual API get their own small class instead.
"""
import json
import logging

import requests

from .base import ConnectorResult, EInvoiceConnector, register
from ..tools import trial
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# status words of the providers -> our status
STATUS_WORDS = {
    'accepted': 'accepted', 'delivered': 'accepted', 'success': 'accepted', 'completed': 'accepted',
    'reported': 'accepted', 'cleared': 'accepted', 'valid': 'accepted',
    'rejected': 'rejected', 'invalid': 'rejected', 'failed': 'rejected', 'refused': 'rejected',
    'received': 'sent', 'queued': 'sent', 'pending': 'sent', 'processing': 'sent', 'sent': 'sent', 'submitted': 'sent',
}


def _dig(data, path):
    """'data.status' -> data['data']['status'] (None when missing)."""
    for key in (path or '').split('.'):
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


@register
class RestConnector(EInvoiceConnector):
    code = 'rest'
    name = 'Generic REST API'
    supports_lookup = True

    # ------------------------------------------------------------------ http
    def _headers(self, content_type='application/json'):
        provider = self.provider.sudo()
        headers = {'Accept': 'application/json', 'Content-Type': content_type}
        if provider.api_key:
            headers[provider.auth_header or 'Authorization'] = '%s%s' % (provider.auth_prefix or '', provider.api_key)
        return headers

    def _url(self, path, **values):
        base = (self.provider.api_url or '').rstrip('/')
        return base + '/' + (path or '').format(**values).lstrip('/')

    def _request(self, method, url, body=None, content_type='application/json'):
        headers = self._headers(content_type)
        summary = '%s %s headers=%s' % (method, url, json.dumps(self.redact_headers(headers)))
        try:
            response = requests.request(method, url, data=body, headers=headers, timeout=self.provider.timeout or 30)
        except requests.RequestException as e:
            # the exception text never contains the headers
            return None, summary, ConnectorResult(ok=False, status='error', message='Connection failed: %s' % e,
                                                  request=summary)
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response, summary, data

    def _result(self, response, summary, data, default_status):
        if response is None:
            return data   # already a ConnectorResult (connection error)
        text = self.truncate(response.text)
        provider = self.provider
        if response.status_code >= 400:
            message = _dig(data, provider.json_message_key or 'message') or response.reason
            status = 'rejected' if response.status_code in (400, 422) else 'error'
            return ConnectorResult(ok=False, status=status, message='HTTP %s: %s' % (response.status_code, message),
                                   request=summary, response=text)
        raw = str(_dig(data, provider.json_status_key or 'status') or '').lower()
        return ConnectorResult(
            ok=True, status=STATUS_WORDS.get(raw, default_status),
            reference=str(_dig(data, provider.json_id_key or 'id') or ''),
            message=str(_dig(data, provider.json_message_key or 'message') or raw or 'OK'),
            request=summary, response=text, details=data if isinstance(data, dict) else {})

    # ------------------------------------------------------------------ API
    def _trial_guard(self):
        # trial build: the generic REST connector (a real Accredited Service Provider) is a full-version feature
        raise UserError(_(
            'Connecting a real Accredited Service Provider (REST) is available in the full version only. '
            'This trial works with the simulation provider: %(used)s/%(max)s documents used. WhatsApp %(phone)s '
            'or scan the QR code in the dashboard to buy the full version.',
            used=trial.documents_used(self.provider.env),
            max=trial.TRIAL_MAX_DOCUMENTS, phone='+' + trial.WHATSAPP_NUMBER))

    def test_connection(self):
        self._trial_guard()
        if not self.provider.api_url:
            return ConnectorResult(ok=False, status='error', message='The API URL is missing.')
        response, summary, data = self._request('GET', self._url(self.provider.test_path or '/'))
        return self._result(response, summary, data, 'ok')

    def send(self, xml_content, document):
        self._trial_guard()
        url = self._url(self.provider.send_path or '/documents', **document)
        response, summary, data = self._request('POST', url, body=xml_content, content_type='application/xml')
        return self._result(response, summary, data, 'sent')

    def get_status(self, reference):
        url = self._url(self.provider.status_path or '/documents/{reference}', reference=reference)
        response, summary, data = self._request('GET', url)
        result = self._result(response, summary, data, 'sent')
        result.reference = result.reference or reference
        return result

    def lookup_participant(self, scheme, identifier):
        self._trial_guard()
        if not self.provider.lookup_path:
            return super().lookup_participant(scheme, identifier)
        url = self._url(self.provider.lookup_path, scheme=scheme, identifier=identifier)
        response, summary, data = self._request('GET', url)
        if response is None:
            return data
        if response.status_code >= 400 and response.status_code != 404:
            return self._result(response, summary, data, 'error')
        registered = response.status_code == 200
        if registered and self.provider.json_registered_key:
            registered = bool(_dig(data, self.provider.json_registered_key))
        return ConnectorResult(ok=True, status='ok', details={'registered': registered}, request=summary,
                               response=self.truncate(response.text),
                               message='Registered on Peppol.' if registered else 'Not found on Peppol.')
