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
COMPANY = "_Test Company"
ROW_AMOUNT = 10
REFERENCE = "TEST-PES-GUARD-0001"


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


def _gateway_account_fixture(settings):
	"""The Payment Gateway Account the guard should resolve for _Test Company."""
	return frappe.db.get_value(
		"Payment Gateway Account",
		{"payment_gateway": GATEWAY, "company": COMPANY},
		["name", "payment_account"],
		as_dict=True,
	)


def _integration_request(status, amount):
	frappe.get_doc(
		{
			"doctype": "Integration Request",
			"integration_request_service": "Pesepay",
			"status": status,
			"reference_doctype": "Sales Invoice",
			"reference_docname": REFERENCE,
			"data": json.dumps({"amount": str(amount), "payment_gateway": GATEWAY}),
		}
	).insert(ignore_permissions=True, ignore_links=True)


def _invoice(account, amount=ROW_AMOUNT, company=COMPANY):
	# A saved POS/Sales Invoice always has a name by the time before_submit runs;
	# the guard only reads fields off the doc, so a named unsaved doc is enough.
	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": company,
			"name": REFERENCE,
			"payments": [{"mode_of_payment": GATEWAY, "account": account, "amount": amount}],
		}
	)
	return doc


class TestPesepaySubmitGuard(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.settings = _settings_fixture()
		cls.pga = _gateway_account_fixture(cls.settings)
		if not cls.pga:
			raise RuntimeError("fixture failed: no Payment Gateway Account for _Test Company")

	def test_submit_blocked_without_completed_pesepay_payment(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		doc = _invoice(self.pga.payment_account)
		with self.assertRaises(frappe.ValidationError):
			validate_pesepay_payment_confirmed(doc)

	def test_submit_allowed_when_completed_payment_covers_row(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		_integration_request("Completed", ROW_AMOUNT)
		doc = _invoice(self.pga.payment_account)
		validate_pesepay_payment_confirmed(doc)  # must not raise

	def test_submit_blocked_when_completed_payment_amount_too_low(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		_integration_request("Completed", ROW_AMOUNT / 2)
		doc = _invoice(self.pga.payment_account)
		with self.assertRaises(frappe.ValidationError):
			validate_pesepay_payment_confirmed(doc)

	def test_ignores_payment_rows_not_mapped_to_pesepay(self):
		from pesepay.overrides.invoice import validate_pesepay_payment_confirmed

		cash = frappe.db.get_value("Account", {"company": COMPANY, "account_name": "Cash"}, "name")
		if not cash:
			self.skipTest("no Cash account in _Test Company")
		doc = _invoice(cash)
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


class TestPesepayCompanySetup(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.settings = _settings_fixture()

	def test_new_company_gets_gateway_account_and_mode_of_payment_account(self):
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "Pesepay Guard Test Co",
				"abbr": "_PGTC",
				"default_currency": "USD",
				"country": "United States",
				"chart_of_accounts": "Standard",
			}
		).insert()

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
