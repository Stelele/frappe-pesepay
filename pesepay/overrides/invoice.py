import json

import frappe
from frappe import _
from frappe.utils import flt, fmt_money

GATEWAY_SETTINGS_DOCTYPE = "Pesepay Settings"

PENDING_IR_STATUS = ("", "Queued", "Authorized")


def _is_pesepay_gateway(payment_gateway):
	"""Whether *payment_gateway* resolves to PesePay gateway settings."""
	if not payment_gateway:
		return False
	return (
		frappe.db.get_value("Payment Gateway", payment_gateway, "gateway_settings")
		== GATEWAY_SETTINGS_DOCTYPE
	)


def _get_pesepay_gateway_account(doc, payment):
	"""Return the PesePay Payment Gateway Account backing a payment row, or None."""
	account = payment.get("account")
	if not account or not doc.get("company"):
		return None
	pga = frappe.db.get_value(
		"Payment Gateway Account",
		{"payment_account": account, "company": doc.company},
		["name", "payment_gateway", "payment_account", "payment_channel"],
		as_dict=True,
	)
	if not pga or not _is_pesepay_gateway(pga.get("payment_gateway")):
		return None
	return pga


def get_pesepay_payments(doc):
	"""All payment rows on *doc* that map to a PesePay gateway account."""
	rows = []
	for pay in doc.get("payments") or []:
		if not flt(pay.get("amount")):
			continue
		pga = _get_pesepay_gateway_account(doc, pay)
		if pga:
			rows.append({"row": pay, "gateway_account": pga})
	return rows


def _ir_data(ir):
	try:
		data = json.loads(ir.get("data")) if isinstance(ir.get("data"), str) else (ir.get("data") or {})
	except json.JSONDecodeError, TypeError:
		data = {}
	return data or {}


def _find_completed_irs(invoice_doctype, invoice_name, payment_gateway):
	"""All Completed PesePay Integration Requests for an invoice + gateway."""
	filters = {
		"integration_request_service": "Pesepay",
		"reference_doctype": invoice_doctype,
		"reference_docname": invoice_name,
		"status": "Completed",
	}
	iris = frappe.get_all(
		"Integration Request",
		filters=filters,
		fields=["name", "data"],
		order_by="creation asc",
		limit=100,
	)
	out = []
	for ir in iris:
		data = _ir_data(ir)
		if payment_gateway and data.get("payment_gateway") != payment_gateway:
			continue
		out.append(data)
	return out


def _find_ir(invoice_doctype, invoice_name, statuses, payment_gateway=None):
	"""Most recent PesePay Integration Request for an invoice that matches *statuses*."""
	filters = {
		"integration_request_service": "Pesepay",
		"reference_doctype": invoice_doctype,
		"reference_docname": invoice_name,
		"status": ("in", statuses),
	}
	iris = frappe.get_all(
		"Integration Request",
		filters=filters,
		fields=["name", "data"],
		order_by="creation desc",
		limit=10,
	)
	for ir in iris:
		data = _ir_data(ir)
		if payment_gateway and data.get("payment_gateway") != payment_gateway:
			continue
		return ir, data
	return None, {}


@frappe.whitelist()
def get_pos_pesepay_mode_names(company=None):
	"""Mode of Payment names (and their gateways) mapped to PesePay on *company*."""
	if not company:
		company = frappe.defaults.get_user_default("company")
	return frappe.db.sql(
		"""
		select mpa.parent as mode_of_payment, pga.payment_gateway
		from `tabMode of Payment Account` mpa
		join `tabPayment Gateway Account` pga
			on pga.payment_account = mpa.default_account
			and pga.company = mpa.company
		join `tabPayment Gateway` pg
			on pg.name = pga.payment_gateway
		where mpa.company = %s
			and pg.gateway_settings = %s
	""",
		(company, GATEWAY_SETTINGS_DOCTYPE),
		as_dict=True,
	)


@frappe.whitelist()
def get_pesepay_payment_state(invoice_doctype, invoice_name):
	"""State of the PesePay payment for a POS draft invoice.

	Used by the POS client to decide whether to start a new payment,
	resume polling an existing one, or finalize a confirmed one.
	"""
	doc = frappe.get_doc(invoice_doctype, invoice_name)
	entries = get_pesepay_payments(doc)
	empty = {
		"has_payment": False,
		"amount": 0,
		"currency": doc.get("currency"),
		"gateway": "",
		"pay_method_name": "",
		"confirmed_ir": None,
		"pending_ir": None,
		"poll_url": "",
		"reference_number": "",
	}
	if not entries:
		return empty

	pay = entries[0]["row"]
	gateway = entries[0]["gateway_account"]["payment_gateway"]
	row_amount = flt(pay.amount)

	confirmed, confirmed_data = _find_ir(invoice_doctype, invoice_name, ("Completed",), gateway)
	if confirmed is not None and flt(confirmed_data.get("amount", 0)) < row_amount - 0.01:
		confirmed, confirmed_data = None, {}

	pending, pending_data = _find_ir(invoice_doctype, invoice_name, ("Queued", "Authorized", ""), gateway)

	return {
		"has_payment": True,
		"amount": row_amount,
		"currency": doc.get("currency"),
		"gateway": gateway,
		"pay_method_name": pay.mode_of_payment,
		"confirmed_ir": confirmed.name if confirmed else None,
		"pending_ir": pending.name if pending else None,
		"poll_url": str(pending_data.get("poll_url") or "") if pending else "",
		"reference_number": str(pending_data.get("reference_number") or "") if pending else "",
	}


def validate_pesepay_payment_confirmed(doc, method=None):
	"""before_submit hook: block submit while PesePay payment rows are unconfirmed.

	POS lets the cashier type an amount on a PesePay mode of payment and press
	"Complete Order" directly. The money only actually moves through the
	"Pay with Pesepay" flow, which records Completed Integration Requests
	against the invoice. Submitting with rows not covered by completed requests
	means the invoice is marked paid in the books with no real payment behind
	it — refuse it and point the cashier back to the payment flow.

	Integration Requests don't record which payment row they pay for, so rows
	are grouped by gateway and each group's row total must be covered by the
	sum of that gateway's completed (currency-matching) requests.
	"""
	rows_by_gateway = {}
	for entry in get_pesepay_payments(doc):
		gateway = entry["gateway_account"]["payment_gateway"]
		rows_by_gateway.setdefault(gateway, []).append(entry["row"])

	for gateway, rows in rows_by_gateway.items():
		row_total = sum(flt(row.get("amount")) for row in rows)

		confirmed_total = 0
		for data in _find_completed_irs(doc.doctype, doc.name, gateway):
			# Currency is tolerated as missing for older requests recorded
			# before the field was tracked in the request payload.
			if data.get("currency") not in (None, doc.get("currency")):
				continue
			confirmed_total += flt(data.get("amount", 0))

		if confirmed_total < row_total - 0.01:
			frappe.throw(
				_(
					"{0} for {1} has not been collected in PesePay yet. Use the"
					" 'Pay with Pesepay' button to collect the payment, or change"
					" the mode of payment."
				).format(fmt_money(row_total, currency=doc.get("currency")), gateway)
			)


@frappe.whitelist()
def mark_pesepay_payment_confirmed(ir_name):
	"""Idempotently mark a PesePay Integration Request as Completed.

	Called by the POS client once the gateway reports SUCCESS. Only touches the
	payment log, never the invoice document itself.
	"""
	if not ir_name:
		return {"ok": False}

	ir = frappe.get_doc("Integration Request", ir_name)
	if ir.integration_request_service != "Pesepay":
		return {"ok": False}

	if ir.status != "Completed":
		ir.db_set("status", "Completed", update_modified=True)

	return {"ok": True, "reference_number": ir_name}
