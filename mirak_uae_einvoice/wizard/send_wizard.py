# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, api, fields, models


class MirakAeSendWizard(models.TransientModel):
    """Send several e-invoices at once (from the invoice list)."""
    _name = 'mirak.ae.send.wizard'
    _description = 'Send UAE E-Invoices'

    move_ids = fields.Many2many('account.move', string='Documents')
    to_send_count = fields.Integer(compute='_compute_counts')
    skipped_count = fields.Integer(compute='_compute_counts')
    result = fields.Text(readonly=True)
    done = fields.Boolean()

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'account.move':
            values['move_ids'] = [(6, 0, self.env.context.get('active_ids', []))]
        return values

    def _sendable(self):
        return self.move_ids.filtered(lambda m: m.mirak_ae_applicable and m.state == 'posted' and
                                      m.mirak_ae_state not in ('sent', 'accepted'))

    @api.depends('move_ids')
    def _compute_counts(self):
        for wizard in self:
            sendable = wizard._sendable()
            wizard.to_send_count = len(sendable)
            wizard.skipped_count = len(wizard.move_ids) - len(sendable)

    def action_send(self):
        self.ensure_one()
        sent, blocked, failed = [], [], []
        for move in self._sendable():
            if move._mirak_ae_send_one():
                sent.append(move.name)
            elif move.mirak_ae_state == 'draft':
                blocked.append(move.name)
            else:
                failed.append(move.name)
        lines = [_('Sent: %s', len(sent))]
        if blocked:
            lines.append(_('Blocked by the pre-validation (to correct): %s', ', '.join(blocked)))
        if failed:
            lines.append(_('Provider error (retried automatically): %s', ', '.join(failed)))
        self.write({'result': '\n'.join(lines), 'done': True})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new'}
