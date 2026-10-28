# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from ast import literal_eval

from odoo import Command, _, api, fields, models

from ..models.readiness import SECTIONS
from ..tools import trial


class MirakAeReadiness(models.TransientModel):
    _name = 'mirak.ae.readiness'
    _description = 'UAE E-Invoicing Readiness Checker'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    score = fields.Integer(string='Readiness', readonly=True)
    level = fields.Selection([('low', 'Not ready'), ('mid', 'Almost ready'), ('high', 'Ready')], readonly=True)
    ok_count = fields.Integer(readonly=True)
    todo_count = fields.Integer(readonly=True)
    line_ids = fields.One2many('mirak.ae.readiness.line', 'wizard_id', readonly=True)

    @api.model
    def _values_for(self, company):
        checks = company._mirak_ae_readiness_checks()
        score = company._mirak_ae_readiness_score(checks)
        order = {key: index for index, (key, _en, _ar) in enumerate(SECTIONS)}
        lines = []
        for check in sorted(checks, key=lambda c: (c['ok'], order[c['section']], -c['weight'])):
            fix = check['fix']
            lines.append(Command.create({
                'section': check['section'], 'name': check['en'], 'name_ar': check['ar'], 'ok': check['ok'],
                'count': check['count'], 'weight': check['weight'], 'fix_type': fix.get('type'),
                'fix_model': fix.get('model'), 'fix_res_id': fix.get('id') or 0,
                'fix_domain': repr(fix.get('domain') or []),
            }))
        return {
            'score': score,
            'level': 'high' if score >= 90 else 'mid' if score >= 60 else 'low',
            'ok_count': len([c for c in checks if c['ok']]),
            'todo_count': len([c for c in checks if not c['ok']]),
            'line_ids': lines,
        }

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        company = self.env['res.company'].browse(values.get('company_id')) or self.env.company
        values.update(self._values_for(company))
        return values

    def action_refresh(self):
        self.ensure_one()
        self.line_ids.unlink()
        self.write(self._values_for(self.company_id))
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'new', 'name': _('Readiness Checker')}


class MirakAeReadinessLine(models.TransientModel):
    _name = 'mirak.ae.readiness.line'
    _description = 'UAE E-Invoicing Readiness Check'
    _order = 'ok, id'

    wizard_id = fields.Many2one('mirak.ae.readiness', required=True, ondelete='cascade')
    section = fields.Selection([(key, en) for key, en, _ar in SECTIONS], required=True)
    name = fields.Char(string='Check', required=True)
    name_ar = fields.Char(string='التحقّق')
    ok = fields.Boolean(string='OK')
    count = fields.Integer(string='To Fix')
    weight = fields.Integer()
    fix_type = fields.Char()
    fix_model = fields.Char()
    fix_res_id = fields.Integer()
    fix_domain = fields.Char()

    def action_fix(self):
        self.ensure_one()
        if self.fix_type == 'records' and self.fix_model:
            domain = literal_eval(self.fix_domain or '[]')
            return {
                'type': 'ir.actions.act_window', 'name': self.name, 'res_model': self.fix_model,
                'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')], 'domain': domain,
                'target': 'current',
            }
        if self.fix_type == 'record' and self.fix_model and self.fix_res_id:
            return {'type': 'ir.actions.act_window', 'res_model': self.fix_model, 'res_id': self.fix_res_id,
                    'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}
        return self.env['ir.actions.act_window']._for_xml_id('mirak_uae_einvoice.action_mirak_ae_settings')


class MirakAeDashboard(models.TransientModel):
    _name = 'mirak.ae.dashboard'
    _description = 'UAE E-Invoicing Dashboard'

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    count_draft = fields.Integer(compute='_compute_dashboard')
    count_ready = fields.Integer(compute='_compute_dashboard')
    count_sent = fields.Integer(compute='_compute_dashboard')
    count_accepted = fields.Integer(compute='_compute_dashboard')
    count_rejected = fields.Integer(compute='_compute_dashboard')
    count_error = fields.Integer(compute='_compute_dashboard')
    count_total = fields.Integer(compute='_compute_dashboard')
    count_to_send = fields.Integer(compute='_compute_dashboard')
    count_problems = fields.Integer(compute='_compute_dashboard')
    acceptance_rate = fields.Integer(compute='_compute_dashboard')
    mandate_date = fields.Date(related='company_id.mirak_ae_mandate_date')
    days_left = fields.Integer(compute='_compute_dashboard')
    mandate_passed = fields.Boolean(compute='_compute_dashboard')
    score = fields.Integer(compute='_compute_dashboard')
    todo_count = fields.Integer(compute='_compute_dashboard')
    provider_id = fields.Many2one(related='company_id.mirak_ae_provider_id')
    provider_environment = fields.Selection(related='company_id.mirak_ae_provider_id.environment')
    problem_ids = fields.Many2many('account.move', compute='_compute_dashboard')
    trial_html = fields.Html(compute='_compute_trial_html', sanitize=False)

    @api.depends('company_id')
    def _compute_trial_html(self):
        for board in self:
            board.trial_html = trial.trial_banner(self.env, board.company_id.id)

    @api.depends('company_id')
    def _compute_display_name(self):
        for board in self:
            board.display_name = _('Dashboard')

    @api.model
    def action_open(self):
        """Menu entry: a saved dashboard record, so that the page has a proper title and no save button."""
        board = self.create({'company_id': self.env.company.id})
        return {'type': 'ir.actions.act_window', 'name': _('Dashboard'), 'res_model': self._name,
                'res_id': board.id, 'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}

    @api.depends('company_id')
    def _compute_dashboard(self):
        Move = self.env['account.move']
        for board in self:
            company = board.company_id
            domain = [('company_id', '=', company.id), ('move_type', 'in', ('out_invoice', 'out_refund')),
                      ('mirak_ae_state', '!=', False)]
            counts = {state: count for state, count in Move._read_group(domain, ['mirak_ae_state'], ['__count'])}
            for state in ('draft', 'ready', 'sent', 'accepted', 'rejected', 'error'):
                board['count_%s' % state] = counts.get(state, 0)
            board.count_total = sum(counts.values())
            board.count_to_send = counts.get('draft', 0) + counts.get('ready', 0)
            board.count_problems = counts.get('rejected', 0) + counts.get('error', 0)
            decided = counts.get('accepted', 0) + counts.get('rejected', 0)
            board.acceptance_rate = round(100.0 * counts.get('accepted', 0) / decided) if decided else 0
            days = company._mirak_ae_days_to_mandate()
            board.days_left = max(days, 0) if days is not None else 0
            board.mandate_passed = days is not None and days <= 0
            checks = company._mirak_ae_readiness_checks()
            board.score = company._mirak_ae_readiness_score(checks)
            board.todo_count = len([c for c in checks if not c['ok']])
            board.problem_ids = Move.search(domain[:2] + [('mirak_ae_state', 'in', ('rejected', 'error'))],
                                            order='mirak_ae_last_attempt desc, id desc', limit=10)

    def _open_moves(self, states, name):
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_out_invoice_type')
        action.update({
            'name': name,
            'domain': [('company_id', '=', self.company_id.id), ('move_type', 'in', ('out_invoice', 'out_refund')),
                       ('mirak_ae_state', 'in', states)],
            'context': {'default_move_type': 'out_invoice'},
        })
        return action

    def action_open_to_send(self):
        return self._open_moves(['draft', 'ready'], _('E-Invoices to Send'))

    def action_open_sent(self):
        return self._open_moves(['sent'], _('E-Invoices Sent'))

    def action_open_accepted(self):
        return self._open_moves(['accepted'], _('E-Invoices Accepted'))

    def action_open_problems(self):
        return self._open_moves(['rejected', 'error'], _('Rejected or Failed E-Invoices'))

    def action_open_readiness(self):
        return {'type': 'ir.actions.act_window', 'res_model': 'mirak.ae.readiness', 'view_mode': 'form',
                'target': 'new', 'name': _('Readiness Checker'), 'context': {'default_company_id': self.company_id.id}}

    def action_send_pending(self):
        moves = self.env['account.move'].search([('company_id', '=', self.company_id.id), ('state', '=', 'posted'),
                                                 ('move_type', 'in', ('out_invoice', 'out_refund')),
                                                 ('mirak_ae_state', 'in', ('draft', 'ready', 'error'))])
        return {'type': 'ir.actions.act_window', 'res_model': 'mirak.ae.send.wizard', 'view_mode': 'form',
                'target': 'new', 'name': _('Send E-Invoices'),
                'context': {'active_model': 'account.move', 'active_ids': moves.ids}}

    def action_open_import(self):
        return self.env['ir.actions.act_window']._for_xml_id('mirak_uae_einvoice.action_mirak_ae_import_wizard')
