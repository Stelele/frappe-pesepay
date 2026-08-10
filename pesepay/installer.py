import frappe
from frappe import _


def after_install():
	"""Create default Pesepay Settings if none exist, set up Mode of Payment."""
	try:
		# Check if "payments" app is installed
		if "payments" not in frappe.get_installed_apps():
			frappe.log_error(
				_("The 'payments' app is required by Pesepay but is not installed."),
				_("Pesepay Installation"),
			)
			return

		# If ERPNext is installed, create a default Mode of Payment for Pesepay
		if "erpnext" in frappe.get_installed_apps():
			try:
				if not frappe.db.exists("Mode of Payment", "Pesepay"):
					mop = frappe.get_doc(
						{
							"doctype": "Mode of Payment",
							"mode_of_payment": "Pesepay",
							"enabled": 1,
							"type": "General",
						}
					)
					mop.insert(ignore_permissions=True)  # System install — no user session
					frappe.log_error(
						_("Default Pesepay Mode of Payment created successfully."),
						_("Pesepay Installation"),
					)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					_("Pesepay Installation - Mode of Payment creation failed"),
				)

		frappe.log_error(
			_("Pesepay installation completed."),
			_("Pesepay Installation"),
		)

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			_("Pesepay Installation Error"),
		)


def before_uninstall():
	"""Clean up Pesepay-specific data."""
	try:
		# Delete all Pesepay Settings records
		settings_list = frappe.get_all("Pesepay Settings", pluck="name")
		for name in settings_list:
			frappe.delete_doc("Pesepay Settings", name, ignore_permissions=True)  # System uninstall

		# Delete all Payment Gateway records with "Pesepay-" prefix
		gateway_list = frappe.get_all(
			"Payment Gateway",
			filters={"gateway": ["like", "Pesepay-%"]},
			pluck="name",
		)
		for name in gateway_list:
			frappe.delete_doc("Payment Gateway", name, ignore_permissions=True)  # System uninstall

		# Delete Integration Requests with service="Pesepay"
		integration_requests = frappe.get_all(
			"Integration Request",
			filters={"integration_request_service": "Pesepay"},
			pluck="name",
		)
		for name in integration_requests:
			frappe.delete_doc("Integration Request", name, ignore_permissions=True)  # System uninstall

		# If ERPNext, clean up Mode of Payment
		if "erpnext" in frappe.get_installed_apps():
			# Clean up gateway-specific MoPs (Pesepay-{name}) and default MoP
			mop_list = frappe.get_all(
				"Mode of Payment",
				filters={"mode_of_payment": ["like", "Pesepay%"]},
				pluck="name",
			)
			for mop_name in mop_list:
				frappe.delete_doc("Mode of Payment", mop_name, ignore_permissions=True)  # System uninstall

	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			_("Pesepay Uninstallation Error"),
		)
