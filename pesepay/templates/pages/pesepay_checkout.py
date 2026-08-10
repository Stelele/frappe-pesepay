import frappe
from frappe import _

no_cache = 1

EXPECTED_KEYS = (
    "amount", "title", "description", "reference_doctype",
    "reference_docname", "payer_name", "payer_email", "currency",
    "payment_gateway",
)

def get_context(context):
    context.no_cache = 1
    for key in EXPECTED_KEYS:
        if key not in frappe.form_dict:
            frappe.redirect_to_message(
                title=_("Invalid Request"),
                message=_("Missing required payment parameter: {0}").format(key),
            )
            raise frappe.Redirect
        context[key] = frappe.form_dict[key]

    context.amount = frappe.utils.fmt_money(
        amount=context.amount, currency=context.currency
    )

    # Payment methods available
    context.payment_methods = [
        {"code": "EcoCash", "label": "EcoCash"},
        {"code": "InnBucks", "label": "InnBucks"},
        {"code": "Omari", "label": "Omari"},
        {"code": "Visa", "label": "Visa"},
        {"code": "MasterCard", "label": "MasterCard"},
        {"code": "Zimswitch", "label": "Zimswitch"},
    ]


@frappe.whitelist(allow_guest=True)
def make_seamless_payment(
    gateway_name, amount, currency, email, phone_number, customer_name,
    payment_method, reference_doctype, reference_docname, title,
    card_number=None, card_cvv=None, card_expiry=None, card_holder=None,
):
    """Process a seamless (server-to-server) payment."""
    from payments.utils import get_payment_gateway_controller

    if reference_doctype and reference_docname:
        try:
            ref_doc = frappe.get_doc(reference_doctype, reference_docname)
            if hasattr(ref_doc, "grand_total"):
                amount = str(ref_doc.grand_total)
            elif hasattr(ref_doc, "amount"):
                amount = str(ref_doc.amount)
        except frappe.DoesNotExistError:
            pass

    controller = get_payment_gateway_controller(gateway_name)
    settings = frappe.get_doc("Pesepay Settings", controller)

    from pesepay.pesepay.doctype.pesepay_settings.pesepay_connector import PesePayConnector

    connector = PesePayConnector(
        integration_key=settings.integration_key,
        encryption_key=settings.get_password("encryption_key", raise_exception=False) or "",
        use_sandbox=int(settings.use_sandbox or 0),
    )

    pesepay_currency = settings._get_pesepay_currency(currency)

    # Build merchant reference
    import time, uuid
    merchant_ref = f"PES-{int(time.time() * 1000)}-{uuid.uuid4().hex[:12].upper()}"

    # Build result URL
    result_url = frappe.utils.get_url(
        f"/api/method/pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.callback"
        f"?gateway={gateway_name}"
    )

    card_details = None
    if payment_method in ("Visa", "MasterCard", "Zimswitch") and card_number:
        card_details = {
            "number": card_number,
            "cvv": card_cvv or "",
            "expiry": card_expiry or "",
            "holder": card_holder or "",
        }

    # Create Integration Request
    from frappe.integrations.utils import create_request_log
    ir_data = {
        "amount": amount,
        "title": title,
        "reference_doctype": reference_doctype,
        "reference_docname": reference_docname,
        "payer_email": email,
        "payer_name": customer_name,
        "currency": currency,
        "payment_gateway": gateway_name,
        "merchant_reference": merchant_ref,
        "payment_method": payment_method,
    }

    ir = create_request_log(
        {k: v for k, v in ir_data.items() if v is not None},
        service_name="Pesepay",
        name=merchant_ref,
    )
    ir.db_set("status", "Queued")
    frappe.db.commit()

    try:
        result = connector.initiate_seamless_payment(
            method=payment_method,
            currency=pesepay_currency,
            amount=float(amount),
            reason=title or "Payment",
            merchant_ref=merchant_ref,
            email=email,
            phone=phone_number or "",
            customer_name=customer_name or "",
            card_details=card_details,
            result_url=result_url,
        )

        # Store result
        ir_data["reference_number"] = result.get("referenceNumber")
        ir_data["poll_url"] = str(result.get("pollUrl") or "")
        frappe.db.set_value("Integration Request", ir.name, "data", frappe.as_json(ir_data), update_modified=True)
        frappe.db.commit()

        frappe.response["message"] = {
            "success": True,
            "is_paid": result.get("transactionStatus") == "SUCCESS",
            "reference_number": result.get("referenceNumber", ""),
            "poll_url": str(result.get("pollUrl", "")) if result.get("pollUrl") else None,
            "redirect_url": str(result.get("redirectUrl", "")) if result.get("redirectUrl") else None,
            "status": result.get("transactionStatus", ""),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "PesePay Seamless Payment")
        frappe.db.set_value("Integration Request", ir.name, "status", "Failed", update_modified=True)
        frappe.db.commit()
        frappe.response["message"] = {
            "success": False,
            "error": _("Payment processing failed. Please try again."),
        }
