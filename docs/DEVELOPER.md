# PesePay — Developer Guide

## Architecture Overview

The app implements a Zimbabwe payment gateway for Frappe/ERPNext with two payment flows:

- **Seamless (inline) flow**: Server-to-server payment initiation via `make_seamless_payment`, followed by client-side polling for status.
- **Redirect flow**: User is redirected to PesePay's payment page, then callback URL is posted to after payment.

Core components:

| Component | File | Description |
|-----------|------|-------------|
| **DocType definitions** | `pesepay/pesepay/doctype/pesepay_settings/` | Pesepay Settings & Currency Map |
| **Business logic** | `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py` | Document methods, validation, auto-setup hooks |
| **Crypto + HTTP client** | `pesepay/pesepay/doctype/pesepay_settings/pesepay_connector.py` | AES-256-CBC encryption, API client, method code lookup |
| **Webhook endpoint** | `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py` `callback()` | `allow_guest=True`, decrypts payload, updates Integration Request |
| **Scheduler** | `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py` `poll_pending_payments()` | Registered under `scheduler_events.all` in `hooks.py` (≈ every few minutes), polls Queued IRs >60s old |
| **Client JS** | `pesepay/public/js/pesepay_pos.js` | POS "Pay with PesePay" button, dialog, polling |
| **Checkout page** | `templates/pages/pesepay_checkout.py` + `.html` | Seamless payment form (methods, phone/card fields) |
| **Redirect page** | `templates/pages/pesepay_redirect.py` + `.html` | Redirect-initiation page, user redirected to PesePay |

## DocType Schemas

### Pesepay Settings (`pesepay_settings`)

Not a Single DocType (`issingle: 0`) — one record per gateway instance, named by the `gateway_name` field (`autoname: field:gateway_name`).

| Fieldname | Type | Required | Description |
|-----------|------|----------|-------------|
| `gateway_name` | Data | **Yes** | Naming field (`autoname: field:gateway_name`) — the record name equals the `gateway_name` value. |
| `integration_key` | Data | **Yes** | PesePay Integration Key. User-supplied. |
| `encryption_key` | Password | **Yes** | PesePay Encryption Key. User-supplied. Validation is byte-based: `len(key.encode("utf-8")) == 32` (on `validate`). The first 16 characters are used as the AES IV and must encode to exactly 16 UTF-8 bytes — non-ASCII 32-char keys can pass the key check yet fail the IV check. |
| `use_sandbox` | Check | No | `1` = sandbox, `0` = production. Default: `1`. |
| `redirect_url` | Data | No | Custom redirect URL for redirect flow. |
| `currency_map` | Table | No | Child table: `frappe_currency` (Link to Currency) + `pesepay_currency` (Select: USD, ZiG). |

**Child table: Pesepay Currency Map**

| Field | Type | Options | Required |
|-------|------|---------|----------|
| `frappe_currency` | Link | Currency | Yes |
| `pesepay_currency` | Select | USD, ZiG | Yes |

### Pesepay Currency Map (`pesepay_currency_map`)

Standalone table DocType with two fields:

- `frappe_currency` — Link to Currency DocType
- `pesepay_currency` — Select (USD, ZiG), required

### Payment Gateway (auto-created)

- Name: `Pesepay-{gateway_name}`
- Controller: points to `Pesepay Settings`
- Created on `on_update` of Pesepay Settings

### Integration Request (child of Pesepay Settings, tracked by service="Pesepay")

- Stores payment data as JSON in `data` field
- Status: Queued / Authorized / Completed / Failed
- Key fields in data: `reference_number`, `poll_url`, `payment_method`, `amount`, `currency`, `merchant_reference`

## Whitelisted APIs & Public Pages

Endpoints are either `/api/method/<module>.<method>` whitelisted methods or pages served from `pesepay/templates/pages/` at their filename route. Guest access is noted per row. `get_payment_url` is **not** listed — it is an un-whitelisted instance method:

| URL | Method | Access | Description |
|-----|--------|--------|-------------|
| `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback` | POST | `allow_guest=True` | **Webhook endpoint**. PesePay posts encrypted payload. Parameters via `kwargs` or request args: `gateway`, `reference`. |
| `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_status` | GET | `allow_guest=True` | **Poll by poll_url**. Only allows `api.pesepay.com` and `api.test.sandbox.pesepay.com` (SSRF protection). |
| `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_reference` | GET | `@frappe.whitelist()` only — requires login, **no** `allow_guest` | **Poll by reference_number**. Only resolves known PesePay gateways before hitting the API. |
| `/api/method/pesepay.templates.pages.pesepay_checkout.make_seamless_payment` | POST | `allow_guest=True` | **Seamless (inline) payment initiation**. Validates fields, creates Integration Request (Queued), calls PesePay API, returns initiation response for client polling. |
| `/pesepay_redirect` (`templates/pages/pesepay_redirect.py` → `get_context`) | GET | Public page | **Redirect page**. Validates `token`, resolves the gateway, initiates the redirect payment with PesePay, sets `context.redirect_url` to send the user to PesePay's hosted page. |

### Real Signatures (verified against actual code)

**`callback(**kwargs)`** — `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py:226`

```python
@frappe.whitelist(allow_guest=True)
def callback(**kwargs):
    gateway_name = kwargs.get("gateway") or frappe.request.args.get("gateway") or ""
    reference_number = kwargs.get("reference") or frappe.request.args.get("reference") or ""
    # ... decrypts payload, matches referenceNumber/merchantReference, updates IR status
```

**`poll_payment_status(poll_url, gateway_name)`** — `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py:451`

```python
@frappe.whitelist(allow_guest=True)
def poll_payment_status(poll_url, gateway_name):
    # SSRF protection: only api.pesepay.com / api.test.sandbox.pesepay.com
    # Calls connector.poll_payment(poll_url) internally — poll_url is a
    # caller-supplied absolute URL from the initiation response, which is why
    # the SSRF host check matters here (unlike poll_payment_reference, which
    # only looks up payments by reference_number).
```

**`poll_payment_reference(reference_number, gateway_name)`** — `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py:494`

```python
@frappe.whitelist()  # note: NO allow_guest — requires login
def poll_payment_reference(reference_number, gateway_name):
    # Checks _is_known_pesepay_gateway() first, then calls connector.check_payment_status()
```

**`get_context(context)`** — `pesepay/templates/pages/pesepay_redirect.py:7`

```python
def get_context(context):
    # GET page context for /pesepay_redirect?token={merchant_reference}
    # Validates IR, resolves gateway, initiates redirect payment, sets context.redirect_url
```

**`make_seamless_payment(gateway_name, amount, currency, ...)`** — `pesepay/templates/pages/pesepay_checkout.py:39`

```python
@frappe.whitelist(allow_guest=True)
def make_seamless_payment(...):
    # Server-to-server seamless initiation; creates IR, calls PesePay API,
    # returns initiation response for client polling
```

`get_payment_url()` (`pesepay_settings.py:30`) is a plain instance method (`def get_payment_url(self, **kwargs)`) with **no** `@frappe.whitelist` — call it from server-side code, not as an API.

**`poll_pending_payments()`** — `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py:381`

```python
def poll_pending_payments():
    # Scheduler (scheduler_events.all in hooks.py): runs roughly every few minutes (not every minute),
    # polls IRs Queued >60s old; calls connector.check_payment_status()
```

## Hooks (hooks.py)

| Hook | File | Description |
|------|------|-------------|
| `after_install` | `pesepay/installer.py:5` | Requires the `payments` app; creates **only** a default Mode of Payment `Pesepay` (if ERPNext is installed). Does **not** create a Pesepay Settings doc. |
| `before_uninstall` | `pesepay/installer.py:51` | Deletes Pesepay Settings, Payment Gateways, Integration Requests, Mode of Payment. |
| `on_update` | `pesepay/pesepay/doctype/pesepay_settings/pesepay_settings.py:21` | Auto-creates Payment Gateway, Payment Gateway Accounts, Mode of Payment, Mode of Payment Accounts. |
| **scheduler_events.all** | `hooks.py:148-152` | `pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_pending_payments` (full path) — runs roughly every few minutes. |

## Webhook / Callback Handling (in detail)

1. PesePay POSTs to `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback` with JSON body containing a `payload` field (base64-encoded AES-256-CBC ciphertext).
2. The `callback()` method:
   - Reads `gateway` and `reference` from POST body or request args
   - Resolves the `Payment Gateway` record → `Pesepay Settings` DocType
   - Creates `PesePayConnector` with `integration_key`, `encryption_key`, `use_sandbox`
   - Decrypts the payload via `connector._decrypt_response(body)` → gets `referenceNumber`, `transactionStatus`
   - If `referenceNumber` is missing → returns `OK` with HTTP 200 (stops PesePay retries)
   - Searches existing Integration Requests (status: Queued/Authorized) for a matching `reference_number`
   - If found, updates IR status to `Completed` (if SUCCESS) or `Failed`
   - If `is_paid` and ERPNext is installed + reference doctype is `Payment Request` → creates Payment Entry
   - Calls `_process_payment_success()` which runs `on_payment_authorized` on the reference doctype

**Credential fields by real name** (never invent names):

- `integration_key` — PesePay Integration Key (Data field, user-supplied)
- `encryption_key` — PesePay Encryption Key (Password field, user-supplied, must be 32 UTF-8 bytes)

## Scheduler

- **`pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_pending_payments`** (full path) runs roughly every few minutes (registered under `scheduler_events.all` in `hooks.py` — not every minute).
- Polls Integration Requests in **Queued** status older than 60 seconds.
- Calls `connector.check_payment_status()` to check status.
- On **SUCCESS**: marks IR as **Completed** and calls `_process_payment_success()`.
- On **FAILED**: marks IR as **Failed**.

## Development Setup

### 1. Install the app in a Frappe site

```bash
bench get-app https://github.com/Stelele/frappe-pesepay --branch version-16
bench --site <site_name> install-app pesepay
```

### 2. Run the scheduler manually

```bash
bench --site <site_name> run-scheduler
```

### 3. Test the payment flow

- Create a Sales Invoice with a payment row using a PesePay-mapped mode of payment (EcoCash, InnBucks, Omari, Visa, MasterCard, Zimswitch, PayGo).
- Click **Pay with PesePay** in the payment panel.
- Enter phone number and select payment method.
- Click **Pay Now**.
- Observe the polling: POS (`pesepay_pos.js`) polls every 3 seconds, max 60 attempts (180 s); the checkout page (`pesepay_checkout.js`) polls every 5 seconds, max 24 attempts (120 s) and exits early if no `pollUrl` was returned.

### 4. Test the webhook

- Set up a public URL that receives POST requests at `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback`.
- The POST body must contain a `payload` field with a base64-encrypted AES-256-CBC ciphertext.
- The webhook will resolve the gateway, decrypt, match the reference, and update the Integration Request status.

### 5. Test the redirect flow

- Call `get_payment_url()` (instance method on Pesepay Settings, not an API) with payment details.
- Visit the returned `./pesepay_redirect?token={merchant_reference}` URL — the page (in `pesepay/templates/pages/`) initiates the payment with PesePay and sends the user to PesePay's hosted page.
- After payment, PesePay posts to the callback URL.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| **"Encryption key must produce exactly 32 UTF-8 bytes"** | Key doesn't encode to exactly 32 UTF-8 bytes, or its first 16 characters don't encode to exactly 16 UTF-8 IV bytes | Generate an **ASCII** 32-character key; verify `len(key.encode("utf-8")) == 32` and `len(key[:16].encode("utf-8")) == 16` |
| **Pay with PesePay button doesn't appear** | No PesePay-mapped payment row, or no Gateway Account | Ensure payment row exists with mapped mode of payment; company has Payment Gateway Account (created by `on_update`) |
| **Webhook not receiving data** | Callback URL unreachable, payload not encrypted | Verify server receives POST at the callback URL; payload must contain `payload` field (base64) |
| **Polling times out (60 attempts / 180s)** | Payment not confirmed by PesePay within 3 minutes | Default max; extend by modifying `max_attempts` in `pesepay/public/js/pesepay_pos.js` or the scheduler |
| **Currency not supported** | Currency not in `currency_map` and not USD/ZWL | Add currency to **Pesepay Settings > Currency Map** child table |
| **"Payment was declined"** | Gateway declined (insufficient funds, invalid card) | Ask customer to use different payment method or verify card details |
| **SSRF error on polling** | Poll URL points to disallowed host | Only `api.pesepay.com` and `api.test.sandbox.pesepay.com` are allowed |

## Cryptography Details

The app uses AES-256-CBC, ported from the C# SDK:

- **Encryption key**: string that must produce exactly 32 UTF-8 bytes (`len(key.encode("utf-8")) == 32`, byte-based — not character count)
- **IV**: first 16 characters of the key string, UTF-8 encoded (not the first 16 bytes of key bytes — matches C# behaviour). Those 16 characters must encode to exactly 16 UTF-8 bytes: a 32-character key containing non-ASCII characters (multi-byte) passes the key check yet fails the IV check
- **Padding**: PKCS7 (block size 128)
- **Cipher**: `cryptography.hazmat.primitives.ciphers.aes.AES256`
- **Output**: base64-encoded ciphertext

The `_AesCbcPayloadCrypto` class in `pesepay_connector.py:118` implements this exactly.

The `_camel_case()` function (`pesepay_connector.py:97`) recursively converts dict keys to camelCase and drops `None` values, mirroring the C# `JsonSerializerOptions` used in the PesePay SDK.

The `_resolve_method_code()` method (`pesepay_connector.py:367`) looks up the PesePay method code for a `(payment_method, currency)` pair from the `METHOD_CODES` table.