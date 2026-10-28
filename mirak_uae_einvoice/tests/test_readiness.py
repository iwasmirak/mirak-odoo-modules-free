# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo.tests import tagged

from .common import MirakAeCommon


@tagged('post_install', '-at_install', 'mirak_ae')
class TestReadiness(MirakAeCommon):

    def _checks(self):
        return {check['key']: check for check in self.company._mirak_ae_readiness_checks()}

    def test_score_grows_when_fixed(self):
        checks = self._checks()
        self.assertTrue(checks['company_trn']['ok'])
        self.assertTrue(checks['company_legal']['ok'])
        self.assertFalse(checks['provider']['ok'])
        before = self.company._mirak_ae_readiness_score()
        self.company.mirak_ae_provider_id = self.env['mirak.ae.provider'].create({'name': 'Mock', 'connector': 'mock'})
        self.company.mirak_ae_mandate_phase = 'phase_2'
        after = self.company._mirak_ae_readiness_score()
        self.assertGreater(after, before)
        self.assertLessEqual(after, 100)

    def test_unmapped_tax_is_reported_with_a_fix(self):
        tax = self.env['account.tax'].create({'name': 'Odd 7%', 'amount': 7, 'type_tax_use': 'sale',
                                              'company_id': self.company.id})
        check = self._checks()['taxes_category']
        self.assertFalse(check['ok'])
        self.assertIn(tax.id, check['fix']['domain'][0][2])
        wizard = self.env['mirak.ae.readiness'].create({})
        line = wizard.line_ids.filtered(lambda l: l.name == check['en'])
        self.assertTrue(line.name_ar)
        action = line.action_fix()
        self.assertEqual(action['res_model'], 'account.tax')
        self.assertIn(tax.id, action['domain'][0][2])
        self.assertTrue(wizard.todo_count)
        wizard.action_refresh()

    def test_customer_without_trn(self):
        self.env['res.partner'].create({'name': 'No TRN LLC', 'is_company': True, 'customer_rank': 1,
                                        'country_id': self.env.ref('base.ae').id})
        self.assertFalse(self._checks()['customers_trn']['ok'])

    def test_dashboard_counts(self):
        sent = self._ae_invoice([(self.product_goods, 1, 100.0, self.tax_s, 0.0)])
        self._ae_invoice([(self.product_goods, 1, 100.0, self.tax_s, 0.0)])
        sent.mirak_ae_state = 'rejected'
        board = self.env['mirak.ae.dashboard'].create({})
        self.assertEqual(board.count_to_send, 1)
        self.assertEqual(board.count_problems, 1)
        self.assertIn(sent, board.problem_ids)
        self.assertTrue(0 <= board.score <= 100)
        self.assertEqual(board.action_open_problems()['res_model'], 'account.move')
