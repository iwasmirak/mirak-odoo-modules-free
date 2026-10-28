=================================
Mirak UAE E-Invoicing (PINT-AE)
=================================

.. note:: **FREE TRIAL (19.0.1.0.2).** The whole flow can be tested with the simulation provider, but only
   15 documents can be sent and the REST connector (a real Accredited Service Provider) is reserved to the full
   version ($249). A QR code in the dashboard and the settings opens WhatsApp (+974 6699 9180) to buy the full
   version. Compatible with Odoo 17, 18, 19 and 20 (Community and Enterprise).

UAE electronic invoicing for **Odoo 19 Community and Enterprise**: customer invoices and credit notes are
generated in the official **Peppol PINT-AE 1.0.4** format (UBL 2.1), checked before they leave Odoo, and handed to
your **Accredited Service Provider (ASP)**, which delivers them on the Peppol network and reports them to the FTA.

The module depends on ``account`` only. ``l10n_ae`` is supported (its taxes are recognised automatically) but not
required.

Features
========

* **PINT-AE XML** for tax invoices (380), out-of-scope invoices (480), credit notes (381) and credit notes related to
  exempt / out-of-scope supplies (81), with:

  * VAT categories S, Z, E, O and AE (reverse charge), mapped automatically from your Odoo taxes and editable;
  * line amounts with VAT (BTAE-10 / BTAE-08), gross price, price discount and base quantity;
  * exemption reason codes, reverse-charge goods types, HS and service accounting codes, GTIN;
  * the eight transaction-type flags (free zone, deemed supply, summary, continuous supply, disclosed agent,
    e-commerce, exports, ...), invoicing period and frequency of billing;
  * foreign currencies: exchange rate to AED (6 decimals), VAT total in AED and total with VAT in AED;
  * credit note reason (BTAE-03) and reference to the original invoice;
  * payment means (IBAN for credit transfers), due date, Incoterms and delivery address for exports.

* **Pre-validation** before sending, in English and Arabic, each message with the official rule number:

  * the module's own checks (company, customer, lines, document);
  * the OASIS UBL 2.1 XSD (shipped with the module);
  * the official PINT-AE 1.0.4 Schematron rules (shipped with the module, run when the optional ``saxonche``
    Python package is installed).

  A document with errors is never sent.

* **Sending**: statuses *Not sent / Ready / Sent / Accepted / Rejected / Error* with coloured badges, one log line
  and one chatter message per exchange, automatic retries (scheduled action, configurable maximum), status follow-up,
  mass sending from the invoice list, optional sending on posting. A sent invoice cannot be reset to draft: a credit
  note must be issued.

* **Connectors**: an abstract connector, a configurable **REST** connector (URL, header, paths, JSON keys) and a
  **simulation** connector (accept / reject / alternate / error) to test the whole flow before signing with an ASP.
  **The API key is never written to logs, messages or exchange records.**

* **Vendor bills from PINT-AE files**: supplier found from its TRN (or Peppol ID, then name), products from code,
  barcode or name, purchase taxes from the PINT-AE category, document allowances and charges, totals checked,
  duplicates (same UUID) refused.

* **Readiness checker**: a weighted score and the list of what is missing (company identifiers, provider, taxes,
  customers, products, documents), each with a *Fix* button.

* **Dashboard**: documents by status, rejected documents, countdown to your mandate date, readiness score, quick
  actions.

* English and **Arabic** (right-to-left) interface, user and administrator groups, multi-company rules, tests.

Configuration
=============

1. *UAE E-Invoicing > Configuration > Settings*: TRN (15 digits, starting with 1 and ending with 03), Peppol
   participant ID (0235 + TIN), trade licence and issuing authority, emirate, service provider, mandate date.
2. *Configuration > Service Providers*: create your ASP (REST connector), enter the URL and the API key given by the
   provider, adapt the paths and JSON keys to its API, press *Test Connection*. Use the *Simulation* provider to try the
   module first.
3. *Configuration > Tax Categories*: check the PINT-AE category of your sales taxes; give exempt taxes their exemption
   reason code.
4. Products: item type (goods / services), HS or service accounting code, reverse-charge goods type when relevant.
5. Customers: TRN, emirate, trade licence; *Check on Peppol* when your provider offers a participant lookup.
6. Open the *Readiness Checker* and fix the remaining points.

Optional: ``pip install saxonche`` on the Odoo server to run the official Schematron rules inside Odoo.

Usage
=====

Post a customer invoice or credit note, then *Send E-Invoice* (or let the module send it when it is posted). Use
*Check E-Invoice* to see the problems without sending. Rejected documents can be corrected and resent with
*Correct and Resend*.

Vendor e-invoices: *UAE E-Invoicing > Documents > Import Vendor E-Invoices*.

Sources
=======

* OpenPeppol, *PINT-AE billing specification 1.0.4*, examples, code lists and Schematron:
  https://docs.peppol.eu/poac/ae/
* UAE Ministry of Finance, *Electronic Invoice Mandatory Fields*, V1.0, 23 February 2026, referring to Ministerial
  Decisions No. 243 and No. 244 of 2025.
* OASIS, *Universal Business Language 2.1*: https://docs.oasis-open.org/ubl/os-UBL-2.1/

Every generated document of the test suite and of the sample set was validated with the UBL 2.1 XSD and the two
official PINT-AE 1.0.4 Schematron files (PINT-UBL-validation-preprocessed, PINT-jurisdiction-aligned-rules).

Known limits
============

* The module is not an ASP: a contract with an Accredited Service Provider is required to exchange documents with
  the FTA. Every ASP has its own API: the REST connector is configurable, and a dedicated connector can be added in a
  few lines (``connectors/base.py``).
* The default mandate dates (January 2027 / July 2027) come from the phased plan of Ministerial Decision No. 244 of
  2025; confirm your own date with the FTA.
* Not generated in this version: the profit margin scheme (category "standard rate additional VAT"), document-level
  allowances and charges (Odoo lines are used instead), embedded PDF copies.
* Passport as legal registration: put the ISO country code of the passport in *Issuing Authority* (official example
  *Seller-PAS-identifier*).
* Rule ibr-187-ae (minimum number of digits of HS and service accounting codes) is published without a value
  ("X") in PINT-AE 1.0.4: the module does not enforce a length.

Support
=======

Bouchibane Karim (Mirak) · contact@ocloo.tech

License
=======

Odoo Proprietary License v1.0 (OPL-1), see LICENSE.
