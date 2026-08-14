import base64
import json
import time
import uuid

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import call_hook_method, cint, get_url

from payments.utils import create_payment_gateway


class PesepaySettings(Document):
    """PesePay payment gateway settings — multi-instance, supports redirect + seamless flows."""

    def validate(self):
        self._validate_encryption_key()

    def on_update(self):
        gw = f"Pesepay-{self.gateway_name}"
        create_payment_gateway(gw, settings="Pesepay Settings", controller=self.gateway_name)
        call_hook_method("payment_gateway_enabled", gateway=gw)
        if not self.flags.ignore_mandatory:
            self._create_mode_of_payment()
            self._create_gateway_accounts()
            self._create_mode_of_payment_accounts()

    def get_payment_url(self, **kwargs):
        """Return the checkout URL for the PesePay redirect flow.

        Does NOT call PesePay API here — that happens in the redirect page.
        """
        self.validate_transaction_currency(kwargs.get("currency", "USD"))

        reference_doctype = kwargs.get("reference_doctype")
        reference_docname = kwargs.get("reference_docname")
        merchant_ref = f"PES-{int(time.time() * 1000)}-{uuid.uuid4().hex[:12].upper()}"

        # Idempotency: check for existing pending IR for same reference
        if reference_doctype and reference_docname:
            existing = frappe.db.get_value(
                "Integration Request",
                {
                    "reference_doctype": reference_doctype,
                    "reference_docname": reference_docname,
                    "integration_request_service": "Pesepay",
                    "status": ("in", ("", "Queued", "Authorized")),
                },
                "name",
            )
            if existing:
                return get_url(f"./pesepay_redirect?token={existing}")

        # Store ALL payment details in IR for the redirect page to consume
        ir_data = {
            "amount": kwargs.get("amount"),
            "title": kwargs.get("title"),
            "description": kwargs.get("description"),
            "reference_doctype": reference_doctype,
            "reference_docname": reference_docname,
            "payer_email": kwargs.get("payer_email"),
            "payer_name": kwargs.get("payer_name"),
            "order_id": kwargs.get("order_id"),
            "currency": kwargs.get("currency"),
            "payment_gateway": f"Pesepay-{self.gateway_name}",
            "redirect_to": kwargs.get("redirect_to"),
            "merchant_reference": merchant_ref,
        }

        ir = create_request_log(
            {k: v for k, v in ir_data.items() if v is not None},
            service_name="Pesepay",
            name=merchant_ref,
        )
        ir.db_set("status", "Queued")
        frappe.db.commit()

        return get_url(f"./pesepay_redirect?token={merchant_ref}")

    def validate_transaction_currency(self, currency):
        """Check that *currency* is supported by this PesePay instance."""
        valid_currencies = set()
        if hasattr(self, "currency_map") and self.currency_map:
            for row in self.currency_map:
                valid_currencies.add(row.frappe_currency)
        # Fallback: accept USD and ZWL
        if not valid_currencies:
            valid_currencies = {"USD", "ZWL"}
        if currency not in valid_currencies:
            frappe.throw(
                _("Currency {0} is not supported by PesePay gateway '{1}'. Supported: {2}").format(
                    currency, self.gateway_name, ", ".join(sorted(valid_currencies))
                )
            )

    def validate_minimum_transaction_amount(self, currency, amount):
        """Placeholder — PesePay minimum amounts vary by method."""
        pass

    # ---- Payment Processing (called from redirect page / seamless page) ----

    def _get_connector(self):
        from pesepay.pesepay.doctype.pesepay_settings.pesepay_connector import PesePayConnector

        return PesePayConnector(
            integration_key=self.integration_key,
            encryption_key=self.get_password("encryption_key", raise_exception=False) or "",
            use_sandbox=cint(self.use_sandbox),
        )

    def _get_pesepay_currency(self, frappe_currency):
        """Map Frappe currency code to PesePay currency code via the child table."""
        if hasattr(self, "currency_map") and self.currency_map:
            for row in self.currency_map:
                if row.frappe_currency == frappe_currency:
                    return row.pesepay_currency
        # Default mapping
        if frappe_currency == "ZWL":
            return "ZiG"
        return frappe_currency

    def _validate_encryption_key(self):
        key = self.get_password("encryption_key", raise_exception=False)
        if not key:
            return
        key_bytes = len(key.encode("utf-8"))
        if key_bytes != 32:
            frappe.throw(
                _("Encryption key must produce exactly 32 UTF-8 bytes for AES-256 (got {0} bytes).").format(key_bytes)
            )

    def _create_mode_of_payment(self):
        """Create Mode of Payment for this gateway if ERPNext is installed."""
        if "erpnext" not in frappe.get_installed_apps():
            return
        mop_name = f"Pesepay-{self.gateway_name}"
        if not frappe.db.exists("Mode of Payment", mop_name):
            try:
                frappe.get_doc({
                    "doctype": "Mode of Payment",
                    "mode_of_payment": mop_name,
                    "enabled": 1,
                    "type": "Bank",
                }).insert(ignore_permissions=True)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Pesepay Mode of Payment creation failed")

    def _get_companies(self):
        """All companies on the site."""
        return [row.name for row in frappe.get_all("Company", fields=["name"], order_by="name")]

    def _find_pesepay_account(self, gateway, company):
        """The PesePay bank account name for *company*, or None."""
        return frappe.db.get_value(
            "Account",
            {"account_name": gateway, "company": company},
            "name",
        )

    def _create_gateway_accounts(self):
        """Create a Payment Gateway Account for every company on the site.

        POS mode detection joins Mode of Payment Account -> Payment Gateway
        Account -> Payment Gateway, so every company that should accept PesePay
        needs its own Payment Gateway Account row. Without this, the Pay with
        PesePay button silently never renders in POS for those companies.
        """
        if "erpnext" not in frappe.get_installed_apps():
            return
        from erpnext.accounts.utils import create_payment_gateway_account

        gateway = f"Pesepay-{self.gateway_name}"
        for company in self._get_companies():
            try:
                create_payment_gateway_account(
                    gateway,
                    payment_channel="Email",
                    company=company,
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Pesepay Payment Gateway Account creation failed",
                )

    def _create_mode_of_payment_accounts(self):
        """Create a Mode of Payment Account row for every company that has a
        PesePay bank account, defaulting to that company's PesePay account.

        Without these rows the Mode of Payment can't be used in POS for the
        company, so this removes the manual per-company setup step.
        """
        if "erpnext" not in frappe.get_installed_apps():
            return
        gateway = f"Pesepay-{self.gateway_name}"
        if not frappe.db.exists("Mode of Payment", gateway):
            return

        mop = frappe.get_doc("Mode of Payment", gateway)
        existing = {row.company for row in mop.accounts}
        appended = False

        for company in self._get_companies():
            if company in existing:
                continue
            account = self._find_pesepay_account(gateway, company)
            if not account:
                continue
            mop.append(
                "accounts",
                {
                    "company": company,
                    "default_account": account,
                },
            )
            appended = True
        if appended:
            mop.save(ignore_permissions=True)


# ── MODULE-LEVEL FUNCTIONS (whitelisted / scheduler) ──────────────────

@frappe.whitelist(allow_guest=True)
def callback(**kwargs):
    """Webhook endpoint for PesePay payment status callbacks.

    PesePay POSTs to this URL with encrypted payment result.
    URL format: /api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback
    """
    gateway_name = kwargs.get("gateway") or frappe.request.args.get("gateway") or ""
    if not gateway_name:
        frappe.log_error("PesePay webhook received without gateway parameter", "PesePay Webhook")
        frappe.response["http_status_code"] = 400
        return "Missing 'gateway' parameter"

    reference_number = kwargs.get("reference") or frappe.request.args.get("reference") or ""

    try:
        # Resolve gateway settings
        gateway = frappe.get_doc("Payment Gateway", gateway_name)
        settings = frappe.get_doc(gateway.gateway_settings, gateway.gateway_controller)

        from pesepay.pesepay.doctype.pesepay_settings.pesepay_connector import PesePayConnector

        connector = PesePayConnector(
            integration_key=settings.integration_key,
            encryption_key=settings.get_password("encryption_key", raise_exception=False) or "",
            use_sandbox=cint(settings.use_sandbox),
        )

        # PesePay posts JSON with encrypted payload
        if frappe.request.data:
            try:
                body = json.loads(frappe.request.data)
            except (json.JSONDecodeError, TypeError):
                body = {}
        else:
            body = kwargs

        # Require encrypted payload — reject unencrypted requests
        if not body.get("payload"):
            frappe.log_error(
                "PesePay webhook rejected: no encrypted payload", "PesePay Webhook"
            )
            frappe.response["http_status_code"] = 200
            return "OK"

        # Decrypt the webhook payload
        result = connector._decrypt_response(body)

        ref = result.get("referenceNumber") or reference_number
        is_paid = result.get("transactionStatus") == "SUCCESS"

        if not ref:
            frappe.log_error(
                f"PesePay webhook missing referenceNumber. Gateway: {gateway_name}",
                "PesePay Webhook",
            )
            frappe.response["http_status_code"] = 200  # Stop PesePay retries
            return "OK"

        # Find Integration Request by referenceNumber
        # Reference number is stored in IR data after redirect initiation
        ir_name = None
        registry = frappe.get_all(
            "Integration Request",
            filters={
                "integration_request_service": "Pesepay",
                "status": ("in", ("Queued", "Authorized")),
            },
            fields=["name", "data"],
        )
        for ir in registry:
            try:
                ir_data = json.loads(ir.data) if isinstance(ir.data, str) else ir.data or {}
            except (json.JSONDecodeError, TypeError):
                continue
            if ir_data.get("reference_number") == ref:
                ir_name = ir.name
                break

        if not ir_name:
            frappe.log_error(
                f"PesePay webhook: no pending IR found for ref {ref}",
                "PesePay Webhook",
            )
            frappe.response["http_status_code"] = 200
            return "OK"

        integration_request = frappe.get_doc("Integration Request", ir_name)
        status = "Completed" if is_paid else "Failed"

        # Atomic status update
        current_status = frappe.db.get_value("Integration Request", ir_name, "status")
        if current_status in ("Completed", "Failed"):
            frappe.response["http_status_code"] = 200
            return "OK"

        integration_request.db_set("status", status, update_modified=True)
        frappe.db.commit()

        if is_paid:
            _process_payment_success(integration_request, settings, ref)

        frappe.response["http_status_code"] = 200
        return "OK"

    except Exception:
        frappe.log_error(frappe.get_traceback(), "PesePay Webhook Error")
        frappe.response["http_status_code"] = 200  # Stop PesePay retries
        return "OK"


def _process_payment_success(integration_request, settings, reference_number):
    """Handle successful payment: update reference doc and create payment entry."""
    try:
        data = json.loads(integration_request.data) if isinstance(integration_request.data, str) else integration_request.data or {}
    except (json.JSONDecodeError, TypeError):
        data = {}

    reference_doctype = data.get("reference_doctype")
    reference_docname = data.get("reference_docname")

    if not reference_doctype or not reference_docname:
        return

    try:
        # Try on_payment_authorized first (works for web forms, custom doctypes)
        ref_doc = frappe.get_doc(reference_doctype, reference_docname)
        custom_redirect = None
        if hasattr(ref_doc, "on_payment_authorized"):
            try:
                custom_redirect = ref_doc.run_method("on_payment_authorized", "Completed")
            except Exception:
                frappe.log_error(frappe.get_traceback(), "PesePay on_payment_authorized")

        # If ERPNext, try creating a Payment Entry for Payment Request
        if "erpnext" in frappe.get_installed_apps() and reference_doctype == "Payment Request":
            _create_payment_entry(ref_doc, data, reference_number)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "PesePay Payment Processing Error")


def _create_payment_entry(payment_request, data, reference_number):
    """Create a Payment Entry for a successful Payment Request."""
    if payment_request.status == "Paid":
        return  # Already handled

    try:
        payment_request.db_set("status", "Paid")
        payment_request.db_set("reference_no", reference_number)
        payment_request.db_set("reference_date", frappe.utils.nowdate())
        frappe.db.commit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "PesePay Payment Request update failed")


def poll_pending_payments():
    """Scheduler: poll PesePay for Integration Requests in 'Queued' > 60s.

    Called every minute via hooks.py scheduler_events.all.
    """
    from datetime import timedelta
    from pesepay.pesepay.doctype.pesepay_settings.pesepay_connector import PesePayConnector

    try:
        cutoff = frappe.utils.now_datetime() - timedelta(seconds=60)
        pending = frappe.get_all(
            "Integration Request",
            filters={
                "integration_request_service": "Pesepay",
                "status": "Queued",
                "creation": ("<", cutoff.strftime("%Y-%m-%d %H:%M:%S")),
            },
            fields=["name", "data"],
            limit=10,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "PesePay Scheduler Query")
        return

    for ir in pending:
        try:
            ir_data = json.loads(ir.data) if isinstance(ir.data, str) else ir.data or {}
        except (json.JSONDecodeError, TypeError):
            continue

        reference_number = ir_data.get("reference_number")
        if not reference_number:
            continue

        gateway_name = ir_data.get("payment_gateway")
        if not gateway_name:
            continue

        current_status = frappe.db.get_value("Integration Request", ir.name, "status")
        if current_status != "Queued":
            continue

        try:
            gateway = frappe.get_doc("Payment Gateway", gateway_name)
            settings = frappe.get_doc(gateway.gateway_settings, gateway.gateway_controller)
        except Exception:
            continue

        connector = PesePayConnector(
            integration_key=settings.integration_key,
            encryption_key=settings.get_password("encryption_key", raise_exception=False) or "",
            use_sandbox=cint(settings.use_sandbox),
        )

        try:
            result = connector.check_payment_status(reference_number)
            if result.get("transactionStatus") == "SUCCESS":
                frappe.db.set_value("Integration Request", ir.name, "status", "Completed", update_modified=True)
                ir_data["reference_number"] = reference_number
                frappe.db.set_value("Integration Request", ir.name, "data", json.dumps(ir_data), update_modified=True)
                frappe.db.commit()
                _process_payment_success(frappe.get_doc("Integration Request", ir.name), settings, reference_number)
            elif result.get("transactionStatus") == "FAILED":
                frappe.db.set_value("Integration Request", ir.name, "status", "Failed", update_modified=True)
                frappe.db.commit()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "PesePay Scheduler Poll")


@frappe.whitelist(allow_guest=True)
def poll_payment_status(poll_url, gateway_name):
    """Client-side polling endpoint for pending payments.

    Only allows polling to known PesePay API domains to prevent SSRF.
    """
    from urllib.parse import urlparse
    from pesepay.pesepay.doctype.pesepay_settings.pesepay_connector import PesePayConnector

    url = (poll_url or "").strip()
    if not url:
        return {}

    parsed = urlparse(url)
    allowed_hosts = {"api.pesepay.com", "api.test.sandbox.pesepay.com"}
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        frappe.response["http_status_code"] = 403
        return {}

    if parsed.netloc and not parsed.hostname:
        frappe.response["http_status_code"] = 403
        return {}

    if parsed.hostname and parsed.hostname not in allowed_hosts:
        frappe.response["http_status_code"] = 403
        return {}

    try:
        gateway = frappe.get_doc("Payment Gateway", gateway_name)
        settings = frappe.get_doc(gateway.gateway_settings, gateway.gateway_controller)

        connector = PesePayConnector(
            integration_key=settings.integration_key,
            encryption_key=settings.get_password("encryption_key", raise_exception=False) or "",
            use_sandbox=cint(settings.use_sandbox),
        )

        return connector.poll_payment(url)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "PesePay Poll Payment")
        return {}


@frappe.whitelist()
def poll_payment_reference(reference_number, gateway_name):
    """Client-side polling by PesePay reference number (used when no poll_url).

    Only resolves known PesePay payment gateways before hitting the API.
    """
    if not reference_number:
        return {}
    if not _is_known_pesepay_gateway(gateway_name):
        frappe.response["http_status_code"] = 403
        return {}

    try:
        gateway = frappe.get_doc("Payment Gateway", gateway_name)
        settings = frappe.get_doc(gateway.gateway_settings, gateway.gateway_controller)

        connector = PesePayConnector(
            integration_key=settings.integration_key,
            encryption_key=settings.get_password("encryption_key", raise_exception=False) or "",
            use_sandbox=cint(settings.use_sandbox),
        )

        return connector.check_payment_status(reference_number)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "PesePay Poll Reference")
        return {}


def _is_known_pesepay_gateway(gateway_name):
    if not gateway_name:
        return False
    settings = frappe.db.get_value("Payment Gateway", gateway_name, "gateway_settings")
    return settings == "Pesepay Settings"


def get_gateway_controller(doctype, docname, payment_gateway=None):
    """Resolve PesePay Settings record from Payment Gateway registry.

    Used by checkout pages to get gateway credentials.
    """
    if not payment_gateway:
        reference_doc = frappe.get_doc(doctype, docname)
        payment_gateway = reference_doc.payment_gateway
    controller = frappe.db.get_value("Payment Gateway", payment_gateway, "gateway_controller")
    if not controller:
        frappe.throw(_("Payment Gateway {0} not configured").format(payment_gateway))
    return controller
