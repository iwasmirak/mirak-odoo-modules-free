# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.mirak_uae_einvoice.connectors import CONNECTORS, EInvoiceConnector


class _FakeResponse:
    def __init__(self, status_code, data, text=''):
        self.status_code, self._data, self.text, self.reason = status_code, data, text or str(data), 'reason'

    def json(self):
        return self._data


@tagged('post_install', '-at_install', 'mirak_ae')
class TestConnectors(TransactionCase):
    # the trial edition exposes the simulation connector only

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import unittest
        cls.Provider = cls.env['mirak.ae.provider']
        if 'rest' not in [code for code, _label in cls.Provider._fields['connector']._description_selection(cls.env)]:
            raise unittest.SkipTest('the REST connector belongs to the full edition')
        cls.Provider = cls.env['mirak.ae.provider']
        cls.mock = cls.Provider.create({'name': 'Mock', 'connector': 'mock'})
        cls.rest = cls.Provider.create({'name': 'REST', 'connector': 'rest', 'api_url': 'https://asp.example/v1',
                                        'api_key': 'SECRET-KEY-123'})

    def test_registry(self):
        self.assertIn('mock', CONNECTORS)
        self.assertIn('rest', CONNECTORS)
        self.assertTrue(all(issubclass(cls, EInvoiceConnector) for cls in CONNECTORS.values()))

    def test_mock_accept_and_reject(self):
        connector = self.mock._get_connector()
        sent = connector.send(b'<Invoice/>', {'number': 'INV/1'})
        self.assertTrue(sent.ok)
        self.assertEqual(sent.status, 'sent')
        self.assertEqual(connector.get_status(sent.reference).status, 'accepted')
        self.mock.mock_behaviour = 'reject'
        status = self.mock._get_connector().get_status(sent.reference)
        self.assertEqual(status.status, 'rejected')
        self.assertIn('ibr-', status.message)
        self.mock.mock_behaviour = 'error'
        self.assertEqual(self.mock._get_connector().send(b'<Invoice/>', {'number': 'X'}).status, 'error')

    def test_mock_lookup(self):
        connector = self.mock._get_connector()
        self.assertTrue(connector.lookup_participant('0235', '1001234567').details['registered'])
        self.assertFalse(connector.lookup_participant('0235', '9900000098').details['registered'])

    def test_test_connection_records_result(self):
        self.mock.action_test_connection()
        self.assertTrue(self.mock.last_test_ok)

    def test_rest_send_parses_json_and_hides_key(self):
        self.skipTest('the trial edition works with the simulation provider only')
        with patch('odoo.addons.mirak_uae_einvoice.connectors.rest.requests.request',
                   return_value=_FakeResponse(201, {'id': 'DOC-9', 'status': 'RECEIVED'})) as call:
            result = self.rest._get_connector().send(b'<Invoice/>', {'number': 'INV/2'})
        self.assertTrue(result.ok)
        self.assertEqual((result.status, result.reference), ('sent', 'DOC-9'))
        headers = call.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer SECRET-KEY-123')   # sent to the provider...
        self.assertNotIn('SECRET-KEY-123', result.request)                    # ...never kept in the log text
        self.assertIn('***', result.request)

    def test_rest_rejection_and_status(self):
        self.skipTest('the trial edition works with the simulation provider only')
        with patch('odoo.addons.mirak_uae_einvoice.connectors.rest.requests.request',
                   return_value=_FakeResponse(422, {'message': '[ibr-128-ae] bad emirate'})):
            result = self.rest._get_connector().send(b'<Invoice/>', {'number': 'INV/3'})
        self.assertEqual(result.status, 'rejected')
        self.assertIn('ibr-128-ae', result.message)
        with patch('odoo.addons.mirak_uae_einvoice.connectors.rest.requests.request',
                   return_value=_FakeResponse(200, {'status': 'DELIVERED'})):
            status = self.rest._get_connector().get_status('DOC-9')
        self.assertEqual((status.status, status.reference), ('accepted', 'DOC-9'))

    def test_partner_peppol_check(self):
        self.env.company.mirak_ae_provider_id = self.mock
        partner = self.env['res.partner'].create({'name': 'Buyer', 'mirak_ae_endpoint_id': '1001234567'})
        partner.action_mirak_ae_check_peppol()
        self.assertEqual(partner.mirak_ae_peppol_status, 'registered')
