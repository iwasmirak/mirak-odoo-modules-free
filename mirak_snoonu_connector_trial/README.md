# Mirak Connector for Snoonu

Odoo 19 module that keeps the Snoonu marketplace in line with Odoo through the Snoonu Merchant Integration API
(Inventory & Pricing Synchronization: `PATCH /api/v1.0/Product/{businessUnitId}/BulkUpdate`).

## What is sent

| Snoonu field | Odoo source |
|---|---|
| `SKU` | Internal Reference (or barcode), editable per product |
| `Price` (brand) | Sales Price, or the account pricelist |
| `Discount` (brand) | 0, or Sales Price − pricelist price ("Pricelist reduction sent as discount") |
| `BranchConfigurations[].BranchId` | Snoonu Branch ID of each branch |
| `UseDefaults` | "Use Brand Price" on the branch |
| `Price` / `Discount` (branch) | Branch pricelist (only when "Use Brand Price" is off) |
| `Stock` | Branch warehouse quantity (available, on hand or forecasted), floored, minus the safety stock |
| `IsAvailable` | "Visible on Snoonu" and the product is active and can be sold |

## Setup

1. Snoonu > Accounts > New: Business Unit ID, Registered Email and API Key (staging first).
2. Branches tab: Snoonu Branch ID and the warehouse that supplies it.
3. Add Products (from the account, or select products in Inventory > Action > Add to Snoonu).
4. Preview Data, then Send Test Product, and check the item in Snoonu.
5. After Snoonu validates: switch to Production, enter the production URL, turn on Automatic Sync.

## Behaviour

* Delta sync: each product keeps a fingerprint of the last data sent; only changed products are sent.
* Batches (500 products by default), retries with backoff on 429/5xx and network errors.
* Every request and response is logged (kept 30 days); products in error show the reason.
* The scheduled action is switched on only while an account has Automatic Sync on.
* Removing a synced visible product asks to hide it on Snoonu first.

## Tests

`odoo -d <test_db> -i mirak_snoonu_connector --test-enable --test-tags /mirak_snoonu_connector --stop-after-init`
(the Snoonu API is mocked).
