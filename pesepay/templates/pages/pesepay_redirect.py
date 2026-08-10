import frappe
from frappe import _
from frappe.integrations.utils import create_request_log

no_cache = 1

def get_context(context):
    context.no_cache = 1
    token = frappe.form_dict.get("token")

    if not token:
        frappe.throw(_("Missing payment token"))

    # Validate Integration Request
    status = frappe.db.get_value("Integration Request", token, "status")
    if status == "Cancelled":
        frappe.throw(_("Payment token has expired"))

    if status in ("Completed", "Failed"):
        # Already processed — redirect to success/fail
        import frappe.utils
        ir = frappe.get_doc("Integration Request", token)
        if status == "Completed":
            frappe.local.flags.redirect_location = frappe.utils.get_url(
                "/payment-success"
            )
        else:
            frappe.local.flags.redirect_location = frappe.utils.get_url(
                "/payment-failed"
            )
        raise frappe.Redirect

    try:
        ir = frappe.get_doc("Integration Request", token)
        ir_data = frappe.parse_json(ir.data) if isinstance(ir.data, str) else ir.data or {}
    except frappe.DoesNotExistError:
        frappe.throw(_("Invalid payment token"))

    # Resolve gateway settings
    gateway_name = ir_data.get("payment_gateway")
    if not gateway_name:
        frappe.throw(_("Payment gateway not configured"))

    from payments.utils import get_payment_gateway_controller
    controller = get_payment_gateway_controller(gateway_name)
    settings = frappe.get_doc("Pesepay Settings", controller)

    # Map currency
    pesepay_currency = settings._get_pesepay_currency(ir_data.get("currency", "USD"))

    # Build callback URL
    result_url = frappe.utils.get_url(
        f"/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback"
        f"?gateway={gateway_name}"
    )
    return_url = ir_data.get("redirect_to") or frappe.utils.get_url(
        f"/payment-success?doctype={ir_data.get('reference_doctype')}&docname={ir_data.get('reference_docname')}"
    )

    # Call PesePay API
    from pesepay.pesepay.doctype.pesepay_settings.pesepay_connector import PesePayConnector

    connector = PesePayConnector(
        integration_key=settings.integration_key,
        encryption_key=settings.get_password("encryption_key", raise_exception=False) or "",
        use_sandbox=int(settings.use_sandbox or 0),
    )

    try:
        result = connector.initiate_redirect_payment(
            amount=float(ir_data.get("amount", 0)),
            currency=pesepay_currency,
            reason=ir_data.get("title") or ir_data.get("description") or "Payment",
            merchant_ref=ir_data.get("merchant_reference", token),
            result_url=result_url,
            return_url=return_url,
        )

        # Store reference in IR for webhook correlation
        ir_data["reference_number"] = result.get("referenceNumber")
        ir_data["poll_url"] = str(result.get("pollUrl") or "")
        ir_data["pesepay_redirect_url"] = str(result.get("redirectUrl") or "")
        frappe.db.set_value("Integration Request", token, "data", frappe.as_json(ir_data), update_modified=True)
        frappe.db.commit()

        context.redirect_url = result.get("redirectUrl", "")
        context.payment_title = ir_data.get("title", "Payment")

        if not context.redirect_url:
            frappe.redirect_to_message(
                title=_("Payment Error"),
                message=_("Could not initiate payment with PesePay. Please try again."),
            )
            raise frappe.Redirect

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "PesePay Redirect Initiation")
        # Leave IR as Queued so user can retry
        context.redirect_url = ""
        context.payment_title = ir_data.get("title", "Payment")
        frappe.redirect_to_message(
            title=_("Payment Error"),
            message=_("Could not initiate payment with PesePay. Please try again."),
        )
        raise frappe.Redirect
