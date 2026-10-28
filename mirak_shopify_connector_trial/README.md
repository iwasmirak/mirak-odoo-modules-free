# Mirak Connector for Shopify — Setup Guide

Two-way synchronization between Odoo 19 (Community or Enterprise) and one or several Shopify stores:
products, variants, images, tags, collections, metafields, translations, stock at several locations,
orders (including edits, POS orders, cancellations), customers, invoices, payments, refunds, returns,
Shopify Payments payouts, abandoned checkouts and real-time updates.

---

## 1. Install the module

1. Copy the `mirak_shopify_connector` folder into your Odoo addons path.
2. Restart Odoo, then in **Apps** click **Update Apps List**, search for *Mirak Connector* and click **Install**.
   Sales and Inventory are installed automatically if needed.

A new menu **Mirak Connector** appears. It opens on the dashboard.

## 2. Create the Shopify app

The connector talks to Shopify through an app that you own, created in the Shopify **Dev Dashboard**.

1. Go to <https://dev.shopify.com/dashboard> (log in with the account that owns your store) and click **Create app**.
2. Give it a name (for example *Odoo*).
3. Create a **version** of the app and select the **Admin API access scopes**:

   **Always needed**

   | Scope | Used for |
   |---|---|
   | `write_products` (includes `read_products`) | products, variants, prices, images, tags, collections, metafields, market prices |
   | `write_inventory` (includes `read_inventory`) | stock quantities |
   | `read_locations` | choosing the Shopify locations |
   | `write_files` (includes `read_files`) | deleting images removed in Odoo |
   | `write_orders` (includes `read_orders`) | importing orders, cancelling orders from Odoo, abandoned checkouts |
   | `read_customers` | customer name, email and phone of the orders |
   | `write_fulfillments` | marking orders as fulfilled |
   | `write_merchant_managed_fulfillment_orders` | marking orders as fulfilled, order location |

   **Only for the options you use**

   | Scope | Option |
   |---|---|
   | `write_customers` | sending Odoo contacts to Shopify |
   | `read_returns`, `write_returns` | returns in either direction |
   | `read_shopify_payments_payouts` | importing Shopify Payments payouts |
   | `read_locales`, `write_translations` | translations |

   Without `read_all_orders`, Shopify only gives access to the orders of the last 60 days. This is
   enough for a live connection. Ask Shopify for this scope if you need older orders.
4. **Release** the version, then **Install** the app on your store and approve the permissions.
5. In the app's **Settings**, copy the **Client ID** and the **Client secret**.

> Keep the Client secret private. If it leaks, rotate it in the Dev Dashboard and enter the new one in Odoo.
> **Test Connection** lists the scopes that are missing for the options you switched on.

## 3. Connect the store in Odoo

1. **Mirak Connector ▸ Stores ▸ New**.
2. **Shopify domain**: your `xxxx.myshopify.com` address (Shopify admin ▸ Settings ▸ Domains).
3. **Authentication**, two possibilities:
   * *Client ID and Secret*: paste the two values. The access token is obtained and renewed automatically.
   * *Install link (Connect with Shopify)*: paste the two values, set the **Public Odoo URL** (HTTPS), add
     `https://<your odoo>/mirak_shopify/oauth/callback` to the app's allowed redirection URLs, then click
     **Connect with Shopify** and approve the app in Shopify.
4. *Direct-sale edition only:* paste your **license key**.
5. Click **Test Connection**. Missing permissions are listed if any.
6. **Stock** tab: choose the main **Shopify location** and the **Odoo warehouse** to synchronize.

## 4. Choose the options

| Tab | Main options |
|---|---|
| **Products** | directions, synchronized data (name, description, price, SKU/barcode, status, images), which new Odoo products are sent (all, categories, ticked only), status of new products (active or draft), price from the sales price or a pricelist, **compare-at price**, conflict policy, **tags**, **Odoo category ↔ product type**, **category collections**, **translations**, **Shopify Markets price lists**, **metafields** |
| **Stock** | directions, free or on-hand quantity, safety buffer, percentage published, reference for the first link, **other warehouses added to the main location** |
| **Orders** | import from a date, all or paid orders, taxes, sales team, cancellations both ways, shipping (fulfillment either way), **returns** both ways, **POS orders**, **customers**, **abandoned checkouts → CRM** |
| **Invoicing** | when to invoice (imported, paid, delivered), payment journal per payment method, credit notes for refunds, sales/shipping/refund accounts, invoice emails, **Shopify Payments payouts** |
| **Shopify Locations** | other Shopify locations, each with its own Odoo warehouse |

### Prices
* **Compare-at price** (the crossed-out price): from the sales price when your Shopify price comes from a
  promotional pricelist, or from a second pricelist. It is only sent when higher than the price.
* **Shopify Markets**: click **Fetch Shopify Price Lists**, then choose an Odoo pricelist for each market
  price list. Its prices are converted to the market currency and sent as fixed prices.
* **Other currency**: if the Shopify store's currency differs from the Odoo company currency, prices are
  converted with the Odoo exchange rates, in both directions.

### Tags, product types and collections
* **Tags**: Odoo product tags ↔ Shopify tags. The first time Odoo sends the tags of a product, the
  Shopify tags are merged into Odoo first, so tags used by your automated collections are never lost.
* **Product category**: the Odoo category becomes the Shopify product type (and, in two-way mode, a
  Shopify product type becomes an Odoo category, created under *Shopify* if needed).
* **Category collections**: products are put in a manual collection named like their Odoo category.

### Metafields
Map an Odoo product field (text, number, true/false, selection) to a Shopify metafield
(`namespace.key` and type), in one or both directions.

### Translations
Product names and descriptions in the other Odoo languages are sent to the Shopify languages with the
same code (for example Odoo *Arabic* → Shopify `ar`).

### Taxes
With *Taxes charged by Shopify*, each Shopify tax rate uses the Odoo sales tax with the same percentage
and the same *included in price* setting. If such a tax does not exist, the order waits and the log says
which tax to create.

### Several locations
The main location (Stock tab) is synchronized with the store warehouse. In **Shopify Locations**, tick
other locations and give each one its Odoo warehouse. Orders fulfilled from a location are delivered from
its warehouse. A location can also publish the stock of other warehouses.

### Orders
* **Edits** made in Shopify (items added or removed, quantities) are applied to the Odoo order as long as
  nothing was delivered or invoiced.
* **POS orders** are imported with their shop location and are delivered at once (or not imported).
* **Cancelled in Odoo → Shopify**: optional; choose restock, refund and customer notification.
* **Returns**: a return closed in Shopify gives back to Odoo the restocked quantities (validated return
  receipt); a return validated in Odoo opens a return in Shopify. Nothing is counted twice.

### Customers
* **Import Shopify Customers** creates and updates Odoo contacts from all Shopify customers.
* **Send to Shopify** (Action menu of contacts) creates or updates the Shopify customer.

### Shopify Payments payouts
Each paid payout becomes a journal entry in the payout bank journal: the bank receives the net amount,
fees go to the fees account, and the card payments of its orders are reconciled. For the automatic
reconciliation, set an *Outstanding Receipts* account on the payment method of the card payment journal.

### Abandoned checkouts
With the CRM app, an abandoned checkout of a known customer becomes an opportunity with the recovery
link; it is marked won when the customer completes the order.

### Real-time updates (webhooks)
Set the **Public Odoo URL** (HTTPS) and click **Activate Real-time Updates**. Shopify then notifies Odoo
of new orders, product, stock and customer changes; Odoo handles them within a minute. The regular
synchronization keeps running as a safety net (you may set a longer interval).

## 5. First synchronization

Click **Sync Now**. It runs in the background; follow the progress on the dashboard and in the logs.

* Shopify products are matched to Odoo products by **SKU** (Odoo *Internal Reference*), so an existing
  catalogue is not duplicated. Products without a match are created.
* Large catalogues are imported in several passes; images are caught up progressively.
* Stock: by default the Shopify quantity is kept for products that already exist in Shopify.

## 6. Several stores

Create one store record per Shopify store. Each store has its own warehouses, settings and rules for
new products. A product changed in Odoo is sent to every store; a change made in one Shopify store
reaches the other stores through Odoo.

## 7. Troubleshooting

| Symptom | What to do |
|---|---|
| *Connection to Shopify failed* | Check the domain, the Client ID and Secret, and that the app is installed on the store. |
| *Access scopes missing* | Add the scopes in a new app version, release it and approve it in the store. |
| An order is not imported | Look at **Mirak Connector ▸ Logs**: the reason is shown (for example a missing tax). The order is retried at every synchronization. |
| Invoice not created | The log shows the accounting error (for example an archived income account). Set the accounts in the **Invoicing** tab. Invoicing is retried every hour. |
| Images not deleted in Shopify | Add the `write_files` scope. |
| Real-time updates not received | The Public Odoo URL must be HTTPS and reachable from the internet. |

## Limits

* Up to 3 options and 100 variants per product (Shopify limits).
* Orders of up to 50 lines.
* Products tracked by lot or serial number are not supported for stock.
* Kits (bill of materials of type kit): the quantity published is the one Odoo computes from the components
  (Manufacturing app).

---

Author: Bouchibane Karim · License OPL-1.
Shopify is a trademark of Shopify Inc. This module is not affiliated with or endorsed by Shopify Inc.
