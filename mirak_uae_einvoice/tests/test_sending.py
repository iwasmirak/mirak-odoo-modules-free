# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MirakAeCommon
from .test_connectors import _FakeResponse


@tagged('post_install', '-at_install', 'mirak_ae')
class TestSending(MirakAeCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['mirak.ae.provider'].create({'name': 'Mock', 'connector': 'mock',
                                                            'company_id': cls.company.id})
        cls.company.mirak_ae_provider_id = cls.provider

    def _invoice(self, **kwargs):
        return self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)], **kwargs)

    def _rules(self, move):
        return {i['rule'] for i in move.mirak_ae_issues or [] if i['level'] == 'error'}

    def test_send_then_accepted(self):
        move = self._invoice()
        self.assertEqual(move.mirak_ae_state, 'draft')
        move.action_mirak_ae_send()
        self.assertEqual(move.mirak_ae_state, 'sent')
        self.assertTrue(move.mirak_ae_reference.startswith('MOCK-'))
        self.assertEqual(move.mirak_ae_attempts, 1)
        self.assertTrue(move.mirak_ae_xml_id)
        self.assertEqual(move.mirak_ae_log_ids.result, 'sent')
        move.action_mirak_ae_check_status()
        self.assertEqual(move.mirak_ae_state, 'accepted')
        with self.assertRaises(UserError):
            move.button_draft()
        with self.assertRaises(UserError):
            move.action_mirak_ae_send()

    def test_rejected_then_reset(self):
        self.provider.mock_behaviour = 'reject'
        move = self._invoice()
        move.action_mirak_ae_send()
        move.action_mirak_ae_check_status()
        self.assertEqual(move.mirak_ae_state, 'rejected')
        self.assertIn('ibr-149-ae', move.mirak_ae_message)
        move.action_mirak_ae_reset()
        self.assertEqual(move.mirak_ae_state, 'draft')

    def test_error_retried_by_cron_up_to_the_limit(self):
        self.provider.mock_behaviour = 'error'
        self.company.mirak_ae_max_attempts = 2
        move = self._invoice()
        move.action_mirak_ae_send()
        self.assertEqual((move.mirak_ae_state, move.mirak_ae_attempts), ('error', 1))
        self.env['account.move']._cron_mirak_ae_process()
        self.assertEqual(move.mirak_ae_attempts, 2)
        self.env['account.move']._cron_mirak_ae_process()
        self.assertEqual(move.mirak_ae_attempts, 2)     # limit reached: no more automatic retries
        self.provider.mock_behaviour = 'accept'
        move.action_mirak_ae_send()                      # a user can still send it by hand
        self.assertEqual(move.mirak_ae_state, 'sent')

    def test_prevalidation_blocks_sending(self):
        self.buyer.street = False
        move = self._invoice()
        with self.assertRaises(UserError):
            move.action_mirak_ae_send()
        move.invalidate_recordset()
        self.assertEqual(move.mirak_ae_state, 'draft')
        self.assertFalse(move.mirak_ae_attempts)

    def test_prevalidation_rules(self):
        self.buyer.write({'street': False, 'mirak_ae_legal_authority': False})
        self.tax_e.mirak_ae_exemption_code = False
        move = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_rc, 0.0),
                                 (self.product_service, 1, 100.0, self.tax_e, 0.0)])
        move.action_mirak_ae_prevalidate()
        rules = self._rules(move)
        for rule in ('ibr-144-ae', 'ibr-101-ae', 'ibr-167-ae'):
            self.assertIn(rule, rules)
        self.assertTrue(all(i['ar'] and i['en'] for i in move.mirak_ae_issues))
        self.assertIn('ibr-144-ae', move.mirak_ae_issues_html)

    def test_credit_note_reason_required(self):
        invoice = self._invoice()
        refund = self._ae_invoice([(self.product_goods, 1, 1000.0, self.tax_s, 0.0)], move_type='out_refund',
                                  reversed_entry_id=invoice.id)
        refund.action_mirak_ae_prevalidate()
        self.assertIn('ibr-158-ae', self._rules(refund))
        refund.mirak_ae_credit_reason = 'DL8.61.1.A'
        refund.action_mirak_ae_prevalidate()
        self.assertEqual(self._rules(refund), set())
        self.assertEqual(refund.mirak_ae_state, 'ready')

    def test_auto_send_on_post(self):
        self.company.mirak_ae_auto_send = True
        move = self._invoice()
        self.assertEqual(move.mirak_ae_state, 'ready')
        self.env['account.move']._cron_mirak_ae_process()
        self.assertIn(move.mirak_ae_state, ('sent', 'accepted'))

    def test_mass_send_wizard(self):
        moves = self._invoice() | self._invoice()
        wizard = self.env['mirak.ae.send.wizard'].with_context(active_model='account.move',
                                                               active_ids=moves.ids).create({})
        self.assertEqual(wizard.to_send_count, 2)
        wizard.action_send()
        self.assertEqual(set(moves.mapped('mirak_ae_state')), {'sent'})

    def test_api_key_never_logged(self):
        self.skipTest('the trial edition works with the simulation provider only')
        rest = self.env['mirak.ae.provider'].create({'name': 'REST', 'connector': 'rest', 'company_id': self.company.id,
                                                     'api_url': 'https://asp.example/v1', 'api_key': 'TOP-SECRET-42'})
        self.company.mirak_ae_provider_id = rest
        move = self._invoice()
        with patch('odoo.addons.mirak_uae_einvoice.connectors.rest.requests.request',
                   return_value=_FakeResponse(201, {'id': 'DOC-1', 'status': 'RECEIVED'})):
            move.action_mirak_ae_send()
        self.assertEqual(move.mirak_ae_state, 'sent')
        texts = ' '.join(filter(None, [move.mirak_ae_message] + move.mirak_ae_log_ids.mapped('request') +
                                move.mirak_ae_log_ids.mapped('response') + move.message_ids.mapped('body')))
        self.assertNotIn('TOP-SECRET-42', texts)
