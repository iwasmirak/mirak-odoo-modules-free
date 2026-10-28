# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Connectors between Odoo and an Accredited Service Provider (ASP).

The module is not an ASP: it prepares PINT-AE documents and hands them to the provider chosen by the company,
which delivers them on the Peppol network and reports to the FTA. Every provider exposes its own API, so each one
is a small class derived from :class:`EInvoiceConnector`, registered with :func:`register`.
"""
import abc
from dataclasses import dataclass, field

# connector code -> class
CONNECTORS = {}

# headers never written in logs
SECRET_HEADERS = {'authorization', 'x-api-key', 'api-key', 'apikey', 'x-auth-token', 'cookie'}


def register(cls):
    CONNECTORS[cls.code] = cls
    return cls


@dataclass
class ConnectorResult:
    """What a connector answers. ``status`` is one of: sent, accepted, rejected, error, ok, unknown."""
    ok: bool
    status: str = 'unknown'
    reference: str = ''
    message: str = ''
    request: str = ''          # redacted summary of what was sent (no secret)
    response: str = ''         # provider answer (truncated)
    details: dict = field(default_factory=dict)


class EInvoiceConnector(abc.ABC):
    """Base of every provider connector. ``provider`` is the ``mirak.ae.provider`` record."""

    code = None
    name = None
    supports_lookup = False

    def __init__(self, provider):
        self.provider = provider

    # ------------------------------------------------------------------ API every connector implements
    @abc.abstractmethod
    def test_connection(self):
        """Check that the URL and the key work. -> ConnectorResult"""

    @abc.abstractmethod
    def send(self, xml_content, document):
        """Hand one PINT-AE document to the provider.

        :param bytes xml_content: the UBL 2.1 XML
        :param dict document: {'number', 'uuid', 'type' ('invoice'/'credit_note'), 'sender', 'receiver'}
        :return: ConnectorResult with ``reference`` = the provider's id for later status checks
        """

    @abc.abstractmethod
    def get_status(self, reference):
        """Current status of a sent document (accepted / rejected / sent). -> ConnectorResult"""

    def lookup_participant(self, scheme, identifier):
        """Is this participant registered on Peppol? -> ConnectorResult(details={'registered': bool})"""
        return ConnectorResult(ok=False, status='unsupported',
                               message='This provider does not offer a participant lookup.')

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def redact_headers(headers):
        return {k: ('***' if k.lower() in SECRET_HEADERS else v) for k, v in (headers or {}).items()}

    @staticmethod
    def truncate(text, size=4000):
        text = text if isinstance(text, str) else (text or b'').decode('utf-8', errors='replace')
        return text if len(text) <= size else text[:size] + '…'
