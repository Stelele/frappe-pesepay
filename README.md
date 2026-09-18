### Pesepay

A Pesepay gateway implementation allowing for Zimbabwean processing of Visa, Mastercard, EcoCash, InnBucks, Omari, etc.

### Value Proposition

Pesepay enables Frappe/ERPNext sites to accept payments via the PesePay payments engine, supporting Zimbabwe's most popular payment methods:

- **Card payments**: Visa, MasterCard (USD)
- **Mobile money**: EcoCash, InnBucks, Omari (ZiG/USD)
- **PayGo** (ZiG)

The app handles encryption, webhook callbacks, polling, and ERPNext integration (Mode of Payment, Payment Entries) automatically.

### Installation

#### Via Bench CLI

```bash
# From your bench directory
bench get-app https://github.com/Stelele/frappe-pesepay --branch version-16
bench --site <site_name> install-app pesepay
```

#### Via Frappe Cloud

1. Navigate to **Settings > Apps** in your Frappe site
2. Click **Add App from GitHub**
3. Enter: `Stelele/frappe-pesepay`
4. Install the app on your site
5. The app requires the `payments` app and optionally `erpnext`

### Setup

#### 1. Pesepay Settings DocType

Go to **Pesepay Settings** (via the app menu or **Settings > Pesepay Settings**). This is a **multi-instance DocType** (not Single) — you can create one record per gateway instance, named by **gateway_name** (`autoname: field:gateway_name`).

> Note: installing the app creates only a bare **Mode of Payment** `Pesepay` (when ERPNext is present) — no Pesepay Settings record. Gateway-specific records (Payment Gateway, Gateway Accounts, per-gateway Mode of Payment) are auto-created when you save a Pesepay Settings record (`on_update`, see below).

**Required fields (marked mandatory):**

| Field | Type | Description |
|-------|------|-------------|
| **gateway_name** | Data | Name identifying this gateway instance. Auto-used as naming rule (`field:gateway_name`). |
| **integration_key** | Data | Your PesePay Integration Key (user-supplied). |
| **encryption_key** | Password | Your PesePay Encryption Key (user-supplied, must be 32 UTF-8 bytes for AES-256). |
| **use_sandbox** | Check | Set to `1` for sandbox environment (`api.test.sandbox.pesepay.com`), `0` for production (`api.pesepay.com`). Default: `1`. |
| **redirect_url** | Data | Custom redirect URL for the redirect-based payment flow. |
| **currency_map** | Table | Child table mapping Frappe currencies to PesePay currencies (see below). |

**Currency Map child table:**

| Field | Type | Options | Description |
|-------|------|---------|-------------|
| **frappe_currency** | Link | Currency | Frappe currency code (e.g., USD, ZWL). Required. |
| **pesepay_currency** | Select | USD, ZiG | PesePay currency code. Required. |

The DocType includes three default roles (System Manager, Accounts Manager, Accounts User) with full read/write permissions.

#### 2. Payment Gateway auto-creation

On **on_update** of Pesepay Settings, the app auto-creates:

- A **Payment Gateway** record named `Pesepay-{gateway_name}` linked to `Pesepay Settings`
- **Payment Gateway Account** rows per company (channel: Email)
- **Mode of Payment** `Pesepay-{gateway_name}` (type: Bank, enabled: 1) if ERPNext is installed
- **Mode of Payment Account** rows per company linking to the company's bank account named `Pesepay-{gateway_name}`

#### 3. Credential handling

- **integration_key** and **encryption_key** are user-supplied values provided by PesePay. These are stored as a Data field and a Password field respectively.
- Validation is byte-based (`len(key.encode("utf-8")) == 32`, enforced on `validate` and in `_AesCbcPayloadCrypto`). The first **16 characters** of the key are used as the AES-256-CBC IV, so they must also encode to exactly **16 UTF-8 bytes** — non-ASCII characters (which encode to >1 byte each) successfully pass the 32-byte key check but fail the IV check.
- The first 16 characters of the encryption key serve as the AES-256-CBC IV (matching the C# SDK behaviour).
- Mark these fields as **user-supplied** — never generate or hardcode these values.

### Usage Walkthrough

#### Payment Flow (POS / Sales Invoice)

1. **Open a Sales Invoice or POS Invoice** with payments attached.
2. If a payment row uses a PesePay-mapped mode of payment (EcoCash, InnBucks, Omari, Visa, MasterCard, Zimswitch, PayGo), the **"Pay with PesePay"** button appears in the payment panel.
3. Click **Pay with PesePay** → a dialog opens showing:
   - The amount to pay
   - Payment method buttons (rendered based on the gateway's configured methods)
   - A **Phone Number** field (required for mobile money: EcoCash, InnBucks, Omari)
4. Select the payment method and enter a phone number.
5. Click **Pay Now**.
6. The system:
   - Saves the invoice
   - Calls `pesepay.templates.pages.pesepay_checkout.make_seamless_payment` API
    - Returns a payment initiation response
    - Starts **polling** for payment status: the POS dialog polls every 3 seconds (max 60 attempts = 180s); the web checkout page polls every 5 seconds (max 24 attempts = 120s)
7. **Polling** uses one of two methods:
   - If a `poll_url` was returned: calls `pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_status` with the poll URL
   - Otherwise: calls `pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_reference` with the reference number
8. When the gateway reports **SUCCESS**, the system:
   - Marks the Integration Request as **Completed**
   - Calls `cur_pos.payment.events.submit_invoice()` to finalize the invoice
   - Hides the dialog

#### Redirect-Based Flow

1. Call `get_payment_url()` — an instance method on **Pesepay Settings**, **not** a whitelisted API endpoint — with payment details.
2. The method creates an **Integration Request** (status: Queued) and returns `./pesepay_redirect?token={merchant_reference}` (it does not call the PesePay API itself).
3. The browser is sent to `/pesepay_redirect?token={merchant_reference}` — the page lives in `pesepay/templates/pages/` (no `www/` page). Its `get_context` validates the token, initiates the payment with PesePay, and redirects the user to PesePay's hosted payment page.
4. After payment, PesePay callbacks to `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback` via POST.

#### Webhook / Callback Handling

PesePay posts an encrypted payload to:

```
POST /api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback
```

Parameters expected via `kwargs` or request args:

| Parameter | Source |
|-----------|--------|
| `gateway` | POST body or request arg |
| `reference` | POST body or request arg |

The webhook:

1. Resolves the gateway settings from the `Payment Gateway` record
2. Decrypts the payload using the PesePayConnector (integration_key + encryption_key)
3. Matches the **referenceNumber** or **merchantReference** against existing Integration Requests
4. Updates the Integration Request status to **Completed** (if SUCCESS) or **Failed**
5. If payment is successful, calls `_process_payment_success()` which:
   - Runs `on_payment_authorized` on the reference doctype (if it exists)
   - If the reference doctype is **Payment Request** and ERPNext is installed, creates a Payment Entry

#### Client-Side Polling Endpoints (whitelisted)

| URL | Method | Access | Description |
|-----|--------|--------|-------------|
| `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_status` | GET | `allow_guest=True` | Poll payment status by `poll_url`. Includes SSRF protection — only allows `api.pesepay.com` and `api.test.sandbox.pesepay.com`. |
| `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_reference` | GET | `@frappe.whitelist()` only — requires login, **no** `allow_guest` | Poll payment status by `reference_number`. Only resolves known PesePay gateways before hitting the API. |

#### Scheduler

- **`pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_pending_payments`** (full path) is registered under `scheduler_events.all` in `hooks.py` — runs roughly every few minutes, not every minute.
- Polls Integration Requests in **Queued** status older than 60 seconds.
- Calls `connector.check_payment_status()` to check status (reference-number lookup only; the `poll_url` is ignored by the scheduler).
- On **SUCCESS**: marks IR as **Completed** and calls `_process_payment_success()`.
- On **FAILED**: marks IR as **Failed**.

### Configuration Reference

#### Pesepay Settings fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| gateway_name | Data | **Yes** | Naming field. Auto-generated pattern: `Pesepay-{name}`. |
| integration_key | Data | **Yes** | PesePay Integration Key. User-supplied. |
| encryption_key | Password | **Yes** | PesePay Encryption Key. User-supplied. Must be 32 UTF-8 bytes. |
| use_sandbox | Check | No | `1` = sandbox, `0` = production. Default: `1`. |
| redirect_url | Data | No | Custom redirect URL for redirect flow. |
| currency_map | Table | No | Child table: `frappe_currency` (Link to Currency) + `pesepay_currency` (Select: USD, ZiG). |

#### Allowed currency mappings

The `validate_transaction_currency` method checks that the transaction currency is in the `currency_map`. If no map is configured, it accepts **USD** and **ZWL** by default.

Supported payment method/currency combinations (from `METHOD_CODES` in `pesepay_connector.py`):

| Method | Currency | Method Code |
|--------|----------|-------------|
| EcoCash | USD | PZW211 |
| EcoCash | ZiG | PZW201 |
| InnBucks | USD | PZW212 |
| Visa | USD | PZW204 |
| MasterCard | USD | PZW205 |
| Zimswitch | USD | PZW215 |
| Omari | USD | PZW216 |
| PayGo | ZiG | PZW210 |

### Troubleshooting / FAQ

| Issue | Likely Cause | Resolution |
|-------|-------------|------------|
| **"Encryption key must produce exactly 32 UTF-8 bytes"** | Key does not encode to exactly 32 UTF-8 bytes (`len(key.encode("utf-8")) != 32`), or its first 16 characters don't encode to exactly 16 UTF-8 IV bytes | Generate an **ASCII** 32-character key. Verify `len(key.encode("utf-8")) == 32` and `len(key[:16].encode("utf-8")) == 16`. |
| **Pay with PesePay button doesn't appear** | No payment row with a PesePay-mapped mode of payment, or company has no Payment Gateway Account | Ensure: (a) a payment row exists with a mode of payment that maps to a PesePay gateway, (b) the company has a Payment Gateway Account row created by the `on_update` hook, (c) the company has a Mode of Payment Account row. |
| **Webhook not receiving data** | PesePay cannot reach your callback URL, or the payload is not encrypted | Verify your server can receive POST at `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback`. Ensure the payload contains a `payload` field (base64-encrypted). |
| **Polling times out (60 attempts / 180s)** | Payment not confirmed by PesePay within 3 minutes | This is the default max. Extend by modifying the `max_attempts` in `pesepay/public/js/pesepay_pos.js` or the scheduler in `pesepay_settings.py`. |
| **Currency not supported** | Transaction currency not in `currency_map` and not USD/ZWL | Add the currency to the **Pesepay Settings > Currency Map** child table. |
| **"Payment was declined"** | Gateway declined the transaction (insufficient funds, invalid card, etc.) | Ask the customer to use a different payment method or verify card details. |

### Contributing

1. Install `pre-commit`: `cd apps/pesepay && pre-commit install`
2. Run linting/formatting: `pre-commit run --all-files`
3. This app uses **ruff** for Python linting, **eslint** + **prettier** for JavaScript.
4. Add tests under `pesepay/tests/` if adding new functionality.
5. Bump version in `pyproject.toml` and create a git tag for releases.

### License

MIT (see license.txt)

Copyright (c) Gift Mugweni

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.