# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Test connector: no network. Simulates a provider that accepts or rejects documents."""
import uuid

from .base import ConnectorResult, EInvoiceConnector, register


@register
class MockConnector(EInvoiceConnector):
    code = 'mock'
    name = 'Test provider (simulation)'
    supports_lookup = True

    def _behaviour(self):
        return self.provider.mock_behaviour or 'accept'

    def test_connection(self):
        return ConnectorResult(ok=True, status='ok', message='Simulated provider: connection OK.')

    def send(self, xml_content, document):
        if not xml_content:
            return ConnectorResult(ok=False, status='error', message='Empty document.')
        reference = 'MOCK-%s' % uuid.uuid4().hex[:12].upper()
        request = 'POST mock://documents %s (%s bytes)' % (document.get('number'), len(xml_content))
        if self._behaviour() == 'error':
            return ConnectorResult(ok=False, status='error', message='Simulated provider error (HTTP 503).',
                                   request=request, response='{"error": "service unavailable"}')
        return ConnectorResult(ok=True, status='sent', reference=reference, message='Received by the provider.',
                               request=request, response='{"id": "%s", "status": "RECEIVED"}' % reference)

    def get_status(self, reference):
        behaviour = self._behaviour()
        if behaviour == 'reject' or (behaviour == 'alternate' and int(reference[-1], 16) % 2):
            return ConnectorResult(ok=True, status='rejected', reference=reference,
                                   message='[ibr-149-ae] Simulated rejection: buyer legal registration missing.',
                                   response='{"status": "REJECTED"}')
        return ConnectorResult(ok=True, status='accepted', reference=reference, message='Delivered and reported.',
                               response='{"status": "ACCEPTED"}')

    def lookup_participant(self, scheme, identifier):
        registered = bool(identifier) and not identifier.startswith('99')
        return ConnectorResult(ok=True, status='ok', details={'registered': registered},
                               message='Registered on Peppol.' if registered else 'Not found on Peppol.')
