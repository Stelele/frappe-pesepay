# PesePay — Merchant User Guide

## Installation

### Via Bench CLI

From your bench directory:

```bash
bench get-app https://github.com/Stelele/frappe-pesepay --branch version-16
bench --site <site_name> install-app pesepay
```

### Via Frappe Cloud

1. Navigate to **Settings > Apps** in your Frappe site
2. Click **Add App from GitHub**
3. Enter: `Stelele/frappe-pesepay`
4. Install the app on your site
5. The app requires the `payments` app and optionally `erpnext`

## Setup

### 1. PesePay Settings

Go to **Pesepay Settings** (via the app menu or **Settings > Pesepay Settings**). This is a multi-instance DocType — one record per gateway (`autoname: field:gateway_name`).

**Fields (required/optional as defined in the DocType JSON):**

| Field | Required | Description |
|-------|----------|-------------|
| **gateway_name** | **Yes** | Name identifying this gateway instance. Naming field (`autoname: field:gateway_name`). |
| **integration_key** | **Yes** | Your PesePay Integration Key (user-supplied). |
| **encryption_key** | **Yes** | Your PesePay Encryption Key (user-supplied, must be 32 UTF-8 bytes for AES-256). |
| **use_sandbox** | No | Set to `1` for sandbox (`api.test.sandbox.pesepay.com`), `0` for production (`api.pesepay.com`). Default: `1`. |
| **redirect_url** | No | Custom redirect URL for the redirect-based payment flow. |
| **currency_map** | No | Child table mapping Frappe currencies to PesePay currencies (see below). |

**Currency Map child table:**

| Frappe Currency | PesePay Currency | Required |
|-----------------|------------------|----------|
| USD | USD | Yes |
| ZWL | ZiG | Yes |

*The DocType includes three default roles (System Manager, Accounts Manager, Accounts User) with full read/write permissions.*

### 2. Automatic Setup on Save

> Installing the app creates only a bare **Mode of Payment** `Pesepay` (when ERPNext is present) — no Pesepay Settings record. Everything below is created when you save a Pesepay Settings record.

On **on_update** of PesePay Settings, the app auto-creates:

- A **Payment Gateway** record named `Pesepay-{gateway_name}` linked to `Pesepay Settings`
- **Payment Gateway Account** rows per company (channel: Email)
- **Mode of Payment** `Pesepay-{gateway_name}` (type: Bank, enabled: 1) if ERPNext is installed
- **Mode of Payment Account** rows per company linking to the company's bank account named `Pesepay-{gateway_name}`

## Payment Flow (POS / Sales Invoice)

1. Open a **Sales Invoice** or **POS Invoice** with payments attached.
2. If a payment row uses a PesePay-mapped mode of payment (EcoCash, InnBucks, Omari, Visa, MasterCard, Zimswitch, PayGo), the **"Pay with PesePay"** button appears in the payment panel.
3. Click **Pay with PesePay** → a dialog opens showing:
   - The amount to pay
   - Payment method buttons (EcoCash, InnBucks, Omari, Visa, MasterCard, etc.)
   - A **Phone Number** field (required for mobile money: EcoCash, InnBucks, Omari)
4. Select the payment method and enter a phone number.
5. Click **Pay Now**.
6. The system:
   - Saves the invoice
   - Calls the `make_seamless_payment` API
   - Returns a payment initiation response
    - Starts **polling** for payment status: the POS dialog polls every 3 seconds (max 60 attempts = 180 seconds); the web checkout page polls every 5 seconds (max 24 attempts = 120 seconds)
7. **Polling** uses one of two methods:
   - If a `poll_url` was returned: calls the `poll_payment_status` endpoint with the poll URL
   - Otherwise: calls the `poll_payment_reference` endpoint with the reference number
8. When the gateway reports **SUCCESS**, the system:
   - Marks the Integration Request as **Completed**
   - Calls `cur_pos.payment.events.submit_invoice()` to finalize the invoice
   - Hides the dialog

## Redirect-Based Flow

1. Call the server-side `get_payment_url()` instance method on a Pesepay Settings record with payment details (it is not a whitelisted API).
2. The method creates an **Integration Request** (status: Queued) and returns the `./pesepay_redirect?token={merchant_reference}` page URL (served from `pesepay/templates/pages/`).
3. The page's `get_context` validates the token, calls `initiate_redirect_payment` on PesePay, and redirects the user to PesePay's hosted payment page. (PesePay never POSTs to the redirect page — its webhook POSTs only to the `callback` endpoint below.)
4. After payment, PesePay callbacks to `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback` via POST.

## Webhook / Callback Handling

PesePay posts an encrypted payload to:

```
POST /api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback
```

**Parameters expected via `kwargs` or request args:**

| Parameter | Source |
|-----------|--------|
| `gateway` | POST body or request arg |
| `reference` | POST body or request arg |

**The webhook process:**

1. Resolves the gateway settings from the `Payment Gateway` record
2. Decrypts the payload using the PesePayConnector (integration_key + encryption_key)
3. Matches the `referenceNumber` (from the decrypted payload, falling back to the `reference` request arg) against the `reference_number` stored on existing Integration Requests
4. Updates the Integration Request status to **Completed** (if SUCCESS) or **Failed**
5. If payment is successful, calls `_process_payment_success()` which:
   - Runs `on_payment_authorized` on the reference doctype (if it exists)
   - If the reference doctype is **Payment Request** and ERPNext is installed, creates a Payment Entry

## FAQ

| Issue | Likely Cause | Resolution |
|-------|-------------|------------|
| **"Encryption key must produce exactly 32 UTF-8 bytes"** | Key bytes are not 32 (validation is byte-based: `len(key.encode("utf-8")) == 32`, and the first 16 characters must encode to exactly 16 IV bytes — non-ASCII characters can fail the IV check) | Generate a 32-character ASCII key. Verify `len(key.encode("utf-8")) == 32`. |
| **Pay with PesePay button doesn't appear** | No payment row with a PesePay-mapped mode of payment, or company has no Payment Gateway Account | Ensure: (a) a payment row exists with a mode of payment that maps to a PesePay gateway, (b) the company has a Payment Gateway Account row created by the `on_update` hook, (c) the company has a Mode of Payment Account row. |
| **Webhook not receiving data** | PesePay cannot reach your callback URL, or the payload is not encrypted | Verify your server can receive POST at `/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback`. Ensure the payload contains a `payload` field (base64-encrypted). |
| **Polling times out (60 attempts / 180s)** | Payment not confirmed by PesePay within 3 minutes | `max_attempts` (60) is the **client-side** polling limit in `pesepay/public/js/pesepay_pos.js` only — extend it there if needed. The server-side scheduler (`poll_pending_payments`) keeps polling Queued Integration Requests independently until they complete or fail. |
| **Currency not supported** | Transaction currency not in `currency_map` and not USD/ZWL | Add the currency to the **Pesepay Settings > Currency Map** child table. |
| **"Payment was declined"** | Gateway declined the transaction (insufficient funds, invalid card, etc.) | Ask the customer to use a different payment method or verify card details. |

## Troubleshooting Checklist

- [ ] Encryption key is exactly 32 UTF-8 bytes
- [ ] Pesepay Settings record is saved (triggers auto-creation of gateway/MoP)
- [ ] Company has a Payment Gateway Account row
- [ ] Company has a Mode of Payment Account row (ERPNext)
- [ ] Transaction currency is in the Currency Map (or USD/ZWL by default)
- [ ] Phone number is entered for mobile money methods (EcoCash, InnBucks, Omari)