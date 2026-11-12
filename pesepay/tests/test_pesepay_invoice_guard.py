"""Tests for the Pesepay POS submit guard and company provisioning.

Covers two bugs reported from the bsmitech.jh.erpnext.com POS:

1. A POS checkout could be submitted with a Pesepay mode of payment while the
   customer never actually paid — nothing on the server required a completed
   Pesepay transaction behind the payment row.
2. A company created *after* Pesepay Settings was configured never got its
   Payment Gateway Account, so the "Pay with Pesepay" button silently never
   rendered in POS for that company.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

SETTINGS_NAME = "Guard Test Co"
GATEWAY = f"Pesepay-{SETTINGS_NAME}"
ROW_AMOUNT = 10


def _reference():
	"""Unique invoice reference per call.

	IntegrationTestCase only rolls back after the whole class (setUpClass
	commits to avoid flakes), so test data persists across tests. Every test
	gets its own reference so its Integration Requests can never collide with
	another test's amount-matching queries.
	"""
	return "TEST-PES-GUARD-" + frappe.generate_hash(length=8)


def _company_fixture():
	"""A fresh company with a standard chart of accounts.

	The dev/CI sites are not guaranteed to carry erpnext test fixtures
	(_Test Company), so the tests provision their own company. The standard
	chart creation needs the 'Transit' Warehouse Type, which plain
	`install-app erpnext` does not create (it comes from the setup wizard).
	"""
	name = "Pesepay Guard Test Co"
	if frappe.db.exists("Company", name):
		return frappe.get_doc("Company", name)

	if not frappe.db.exists("Warehouse Type", "Transit"):
		# Warehouse Type is prompt-named: the name must be set explicitly.
		frappe.get_doc({"doctype": "Warehouse Type", "warehouse_type": "Transit", "name": "Transit"}).insert(
			ignore_permissions=True
		)

	return frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": name,
			"abbr": "_PGTC",
			"default_currency": "USD",
			"country": "United States",
			"chart_of_accounts": "Standard",
		}
	).insert()


def _settings_fixture():
	"""A Pesepay Settings doc whose on_update provisions gateway + MoP + PGAs."""
	if frappe.db.exists("Pesepay Settings", SETTINGS_NAME):
		return frappe.get_doc("Pesepay Settings", SETTINGS_NAME)
	return frappe.get_doc(
		{
			"doctype": "Pesepay Settings",
			"gateway_name": SETTINGS_NAME,
			"integration_key": "guard-test-integration-key",
			"encryption_key": "a" * 32,
			"use_sandbox": 1,
		}
	).insert()


def _gateway_account_fixture(settings, company):
	"""The Payment Gateway Account the guard should resolve for *company*."""
	pga = frappe.db.get_value(
		"Payment Gateway Account",
		{"payment_gateway": GATEWAY, "company": company},
		["name", "payment_account"],
		as_dict=True,
	)
	if not pga:
		diagnostics = {
			"settings": frappe.db.get_value(
				"Pesepay Settings", SETTINGS_NAME, ["name", "gateway_name"], as_dict=True
			),
			"gateway": frappe.db.get_value(
				"Payment Gateway", GATEWAY, ["name", "gateway_settings"], as_dict=True
			),
			"company": frappe.db.get_value("Company", company, ["name", "company_name"], as_dict=True),
			"pesepay_accounts": frappe.get_all(
				"Account",
				filters={"company": company, "account_name": ["like", "%Pesepay%"]},
				fields=["name", "account_name", "account_type", "account_currency"],
			),
			"pgas": frappe.get_all(
				"Payment Gateway Account",
				filters={"company": company},
				fields=["name", "payment_gateway", "payment_account", "currency"],
			),
			"mop_rows": frappe.get_all(
				"Mode of Payment Account",
				filters={"parent": GATEWAY, "company": company},
				fields=["company", "default_account"],
			),
			"error_logs": [
				frappe.get_doc("Error Log", log.name).error[:1500]
				for log in frappe.get_all(
					"Error Log",
					filters={"method": ("like", "%Pesepay%")},
					order_by="creation desc",
					limit=3,
					as_list=False,
				)
			],
		}
		raise RuntimeError(
			"fixture failed: no Payment Gateway Account for the test company\n"
			+ json.dumps(diagnostics, indent=1, default=str)
		)
	return pga


def _integration_request(status, amount, reference, currency=None):
	data = {"amount": str(amount), "payment_gateway": GATEWAY}
	if currency:
		data["currency"] = currency
	frappe.get_doc(
		{
			"doctype": "Integration Request",
			"integration_request_service": "Pesepay",
			"status": status,
			"reference_doctype": "Sales Invoice",
			"reference_docname": reference,
			"data": json.dumps(data),
		}
	).insert(ignore_permissions=True, ignore_links=True)


def _invoice(account, reference, amount=ROW_AMOUNT, company=None):
	# A saved POS/Sales Invoice always has a name by the time before_submit runs;
	# the guard only reads fields off the doc, so a named unsaved doc is enough.
	return frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": company,
			"name": reference,
			"currency": "USD",
			"payments": [{"mode_of_payment": GATEWAY, "account": account, "amount": amount}],
		}
	)


class TestPesepaySubmitGuard(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.settings = _settings_fixture()
		cls.company = _company_fixture()
		cls.pga = _gateway_account_fixture(cls.settings, cls.company.name)
		if not cls.pga:
			raise RuntimeError("fixture failed: no Payment Gateway Account for the test company")

	def setUp(self):
		super().setUp()
		self.reference = _reference()

	def test_submit_blocked_without_completed_pesepay_payment(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		doc = _invoice(self.pga.payment_account, self.reference, company=self.company.name)
		with self.assertRaises(frappe.ValidationError):
			validate_pesepay_payment_confirmed(doc)

	def test_submit_allowed_when_completed_payment_covers_row(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		_integration_request("Completed", ROW_AMOUNT, self.reference)
		doc = _invoice(self.pga.payment_account, self.reference, company=self.company.name)
		validate_pesepay_payment_confirmed(doc)  # must not raise

	def test_submit_blocked_when_completed_payment_amount_too_low(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		_integration_request("Completed", ROW_AMOUNT / 2, self.reference)
		doc = _invoice(self.pga.payment_account, self.reference, company=self.company.name)
		with self.assertRaises(frappe.ValidationError):
			validate_pesepay_payment_confirmed(doc)

	def test_submit_blocked_when_completed_payment_in_different_currency(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		_integration_request("Completed", ROW_AMOUNT, self.reference, currency="ZWL")
		doc = _invoice(
			self.pga.payment_account, self.reference, company=self.company.name
		)  # invoice currency is USD
		with self.assertRaises(frappe.ValidationError):
			validate_pesepay_payment_confirmed(doc)

	def test_blocked_when_second_row_is_not_backed_by_a_payment(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		# Two rows on the same gateway, only the first is paid.
		doc = _invoice(self.pga.payment_account, self.reference, company=self.company.name)
		doc.payments.append({"mode_of_payment": GATEWAY, "account": self.pga.payment_account, "amount": 5})
		_integration_request("Completed", ROW_AMOUNT, self.reference)
		with self.assertRaises(frappe.ValidationError):
			validate_pesepay_payment_confirmed(doc)

	def test_allowed_when_payments_cover_all_rows_on_a_gateway(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		doc = _invoice(self.pga.payment_account, self.reference, company=self.company.name)
		doc.payments.append({"mode_of_payment": GATEWAY, "account": self.pga.payment_account, "amount": 5})
		_integration_request("Completed", ROW_AMOUNT, self.reference)
		_integration_request("Completed", 5, self.reference)
		validate_pesepay_payment_confirmed(doc)  # must not raise

	def test_ignores_payment_rows_not_mapped_to_pesepay(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		pesepay_accounts = frappe.get_all(
			"Payment Gateway Account",
			filters={"company": self.company.name},
			pluck="payment_account",
		)
		other_account = frappe.db.get_value(
			"Account",
			{
				"company": self.company.name,
				"is_group": 0,
				"name": ("not in", pesepay_accounts or [""]),
			},
			"name",
		)
		if not other_account:
			self.skipTest("no non-pesepay ledger account in the test company")
		doc = _invoice(other_account, self.reference, company=self.company.name)
		validate_pesepay_payment_confirmed(doc)  # must not raise

	def test_doc_events_wiring(self):
		from pesepay import hooks

		expected = "pesepay.overrides.invoice.validate_pesepay_payment_confirmed"
		for doctype in ("POS Invoice", "Sales Invoice"):
			self.assertEqual(hooks.doc_events[doctype]["before_submit"], expected)
		self.assertEqual(
			hooks.doc_events["Company"]["on_update"],
			"pesepay.overrides.company.setup_pesepay_for_company",
		)

	def test_mark_confirmed_requires_invoice_submit_permission(self):
		from pesepay.overrides.invoice import mark_pesepay_payment_confirmed

		_integration_request("Completed", ROW_AMOUNT, self.reference)
		ir_name = frappe.db.get_value(
			"Integration Request",
			{"reference_docname": self.reference},
			"name",
			order_by="creation desc",
		)

		# A user with no roles must not be able to mark payments as collected.
		email = "pesepay-guard-test-user@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Pesepay Guard",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		try:
			with self.assertRaises(frappe.PermissionError):
				mark_pesepay_payment_confirmed(ir_name)
		finally:
			frappe.set_user("Administrator")

		# The POS cashier (submit permission on the invoice) may.
		mark_pesepay_payment_confirmed(ir_name)
		self.assertEqual(frappe.db.get_value("Integration Request", ir_name, "status"), "Completed")

	def test_blank_reference_requests_cannot_be_confirmed(self):
		"""Requests with no referenced invoice prove nothing — refuse them."""
		from pesepay.overrides.invoice import mark_pesepay_payment_confirmed

		ir = frappe.get_doc(
			{
				"doctype": "Integration Request",
				"integration_request_service": "Pesepay",
				"status": "Queued",
				"data": json.dumps({"amount": str(ROW_AMOUNT), "payment_gateway": GATEWAY}),
			}
		).insert(ignore_permissions=True, ignore_links=True)

		self.assertEqual(mark_pesepay_payment_confirmed(ir.name)["ok"], False)
		self.assertEqual(frappe.db.get_value("Integration Request", ir.name, "status"), "Queued")


class TestPesepayCompanySetup(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.settings = _settings_fixture()

	def test_new_company_gets_gateway_account_and_mode_of_payment_account(self):
		company = _company_fixture()

		self.assertTrue(
			frappe.db.exists(
				"Payment Gateway Account",
				{"payment_gateway": GATEWAY, "company": company.name},
			),
			"new company did not get a Payment Gateway Account",
		)
		mop = frappe.get_doc("Mode of Payment", GATEWAY)
		self.assertIn(
			company.name,
			[row.company for row in mop.accounts],
			"new company did not get a Mode of Payment account row",
		)
