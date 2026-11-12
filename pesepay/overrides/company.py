import frappe
from frappe import _


def setup_pesepay_for_company(doc, method=None):
	"""Company.on_update (during insert): provision PesePay setup for the company.

	Users create companies without knowing that PesePay needs a Mode of Payment
	account row and a Payment Gateway Account per company before the "Pay with
	Pesepay" button will render in POS. Provisioning therefore runs behind the
	scenes whenever a company is added. Every step is idempotent (only creates
	what is missing) and errors are logged instead of raised, so a gateway
	setup problem can never fail company creation.

	Guarded to company creation only (flags.in_insert): the chart of accounts
	the provisioning depends on is created by Company.on_update, which runs
	inside the same insert, and re-running on every company save would fight
	admins who deliberately remove the accounts.
	"""
	if not doc.flags.in_insert:
		return
	if "erpnext" not in frappe.get_installed_apps():
		return

	frappe.logger("pesepay-hook").info(
		f"provisioning start company={doc.name} accounts={frappe.db.count('Account', {'company': doc.name})}"
	)
	for name in frappe.get_all("Pesepay Settings", pluck="name"):
		try:
			settings = frappe.get_doc("Pesepay Settings", name)
			settings._create_mode_of_payment()
			settings._create_gateway_accounts(company=doc.name)
			settings._create_mode_of_payment_accounts(company=doc.name)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				_("Pesepay setup for new company failed"),
			)
	frappe.logger("pesepay-hook").info(
		f"provisioning end company={doc.name} pgas={frappe.db.count('Payment Gateway Account', {'company': doc.name})}"
	)
