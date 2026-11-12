import frappe


def run():
	frappe.set_user("Administrator")

	from pesepay.tests.test_pesepay_invoice_guard import (
		_company_fixture,
		_gateway_account_fixture,
		_settings_fixture,
	)

	def class_sequence(label):
		settings = _settings_fixture()
		company = _company_fixture()
		n_accounts = frappe.db.count("Account", {"company": company.name})
		pga = _gateway_account_fixture(settings, company.name)
		print(f"[{label}] company={company.name} accounts={n_accounts} pga={pga.name if pga else None}")
		return settings, company

	# fresh state: remove leftovers from earlier runs
	for dt, filters in [
		("Payment Gateway Account", {"payment_gateway": ("like", "Pesepay-Guard%")}),
		("Mode of Payment Account", {"parent": ("like", "Pesepay-Guard%")}),
		("Account", {"company": "Pesepay Guard Test Co"}),
		("Warehouse", {"company": "Pesepay Guard Test Co"}),
	]:
		for name in frappe.get_all(dt, filters=filters, pluck="name"):
			frappe.delete_doc(dt, name, ignore_permissions=True, force=True)
	for dt, name in [
		("Mode of Payment", "Pesepay-Guard Test Co"),
		("Payment Gateway", "Pesepay-Guard Test Co"),
		("Pesepay Settings", "Guard Test Co"),
		("Company", "Pesepay Guard Test Co"),
	]:
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, ignore_permissions=True, force=True)
	frappe.db.commit()

	class_sequence("class1")
	frappe.db.commit()

	# simulate IntegrationTestCase._rollback_db at class end
	frappe.db.rollback()
	print(
		"after rollback: settings=%s company=%s accounts=%s"
		% (
			frappe.db.exists("Pesepay Settings", "Guard Test Co"),
			frappe.db.exists("Company", "Pesepay Guard Test Co"),
			frappe.db.count("Account", {"company": "Pesepay Guard Test Co"}),
		)
	)

	class_sequence("class2")
	frappe.db.rollback()
