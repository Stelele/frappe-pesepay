frappe.provide("pesepay.pos");

(function () {
	const MOBILE_METHODS = ["EcoCash", "InnBucks", "Omari"];

	const METHOD_BY_MODE_NAME = {
		ecocash: "EcoCash",
		innbucks: "InnBucks",
		omari: "Omari",
	};

	const CACHE = {
		mode_map: null, // { mode_of_payment: payment_gateway }
	};

	const state = {
		active_dialog: null, // the frappe.ui.Dialog currently open, or null
	};

	// ---------------------------------------------------------------------
	// Mode-of-payment detection (PesePay-gated modes for this company)
	// ---------------------------------------------------------------------

	function fetch_mode_map(company) {
		if (CACHE.mode_map) return Promise.resolve(CACHE.mode_map);
		return frappe
			.call({
				method: "pesepay.overrides.invoice.get_pos_pesepay_mode_names",
				args: { company: company },
			})
			.then((r) => {
				CACHE.mode_map = {};
				(r.message || []).forEach((d) => {
					CACHE.mode_map[d.mode_of_payment] = d.payment_gateway;
				});
				return CACHE.mode_map;
			})
			.catch(() => {
				CACHE.mode_map = {};
				return CACHE.mode_map;
			});
	}

	function get_pesepay_row(frm, mode_map) {
		return (frm.doc.payments || []).find(
			(p) => mode_map[p.mode_of_payment] && flt(p.amount) > 0
		);
	}

	// ---------------------------------------------------------------------
	// State from the server (resume / confirm detection)
	// ---------------------------------------------------------------------

	function get_state(frm) {
		return frappe
			.call({
				method: "pesepay.overrides.invoice.get_pesepay_payment_state",
				args: { invoice_doctype: frm.doc.doctype, invoice_name: frm.doc.name },
			})
			.then((r) => r.message || {});
	}

	// ---------------------------------------------------------------------
	// Entry point: "Pay with Pesepay" button in the POS payment panel
	// ---------------------------------------------------------------------

	function mount_pay_button(frm) {
		if (!window.cur_pos || !cur_pos.payment) return;
		fetch_mode_map(frm.doc.company).then((mode_map) => {
			const has_pesepay_mode = (frm.doc.payments || []).some((p) => mode_map[p.mode_of_payment]);
			if (!has_pesepay_mode) return;

			const container = $(cur_pos.payment.$component).find(".payment-container-left");
			if (!container.length) return;
			if (container.find(".pesepay-pay-button").length) return;

			const $wrap = $(
				`<div class="pesepay-pay-button-wrapper mt-2">
					<button class="btn btn-primary btn-sm w-full pesepay-pay-button">
						${__("Pay with Pesepay")}
					</button>
				</div>`
			);
			$wrap.on("click", () => open_pesepay_dialog(frm));
			container.append($wrap);
		});
	}

	function open_pesepay_dialog(frm) {
		if (state.active_dialog) return; // already paying
		if (frm.doc.docstatus !== 0) return;

		fetch_mode_map(frm.doc.company).then((mode_map) => {
			let row = get_pesepay_row(frm, mode_map);
			if (!row) {
				// No amount entered on the PesePay row yet — fill the remaining
				// amount automatically, then open the dialog for it.
				const remaining = flt(frm.doc.grand_total) - flt(frm.doc.paid_amount);
				const any_row = (frm.doc.payments || []).find((p) => mode_map[p.mode_of_payment]);
				if (!any_row || remaining <= 0) {
					frappe.show_alert({
						message: __("Enter a Pesepay payment amount first."),
						indicator: "orange",
					});
					return;
				}
				frappe.model.set_value(any_row.doctype, any_row.name, "amount", remaining).then(() => {
					open_pesepay_dialog(frm);
				});
				return;
			}

			get_state(frm)
				.then((st) => {
					if (state.active_dialog) return;

					// The server doc may not have the just-entered payment row yet;
					// trust the live client amount for display + initiation.
					st.amount = flt(row.amount);

					// The client already resolved the gateway for this mode; prefer it
					// over server state, which can be empty before the doc is saved.
					st.gateway = mode_map[row.mode_of_payment] || st.gateway;

					// Already confirmed -> let the normal submit happen, no dialog.
					if (st.confirmed_ir) return;

					if (st.pending_ir) {
						open_dialog(frm, st, { merchant_reference: st.pending_ir });
					} else {
						open_dialog(frm, st, {});
					}
				})
				.catch(() => {
					// Invoice not saved yet (no server state); drive from the live
					// client values so the cashier can still pay.
					open_dialog(
						frm,
						{
							amount: flt(row.amount),
							currency: frm.doc.currency,
							gateway: mode_map[row.mode_of_payment],
						},
						{}
					);
				});
		});
	}

	// ---------------------------------------------------------------------
	// Dialog
	// ---------------------------------------------------------------------

	function open_dialog(frm, st, resume) {
		const method = guess_method(frm, st);
		const dlg = new frappe.ui.Dialog({
			title: __("PesePay Mobile Money"),
			size: "small",
			fields: [
				{ fieldtype: "HTML", fieldname: "amount_html" },
				{
					fieldname: "payment_method",
					label: __("Payment Method"),
					fieldtype: "Select",
					options: MOBILE_METHODS,
					default: method,
					reqd: 1,
				},
				{ fieldname: "phone_number", label: __("Phone Number"), fieldtype: "Data", reqd: 1 },
				{ fieldtype: "HTML", fieldname: "status_html" },
			],
			primary_action_label: resume.merchant_reference ? __("Continue") : __("Pay Now"),
			primary_action(values) {
				if (resume.merchant_reference) {
					resume_polling(frm, st, dlg, resume);
				} else {
					pay_now(frm, st, dlg, values);
				}
			},
		});

		state.active_dialog = dlg;
		dlg.onhide = () => {
			state.active_dialog = null;
		};

		dlg.fields_dict.amount_html.$wrapper.html(
			`<div class="mb-2"><strong>${__("Amount")}:</strong> ${format_currency(st.amount, st.currency)}</div>`
		);

		dlg.show();
		return dlg;
	}

	function guess_method(frm, st) {
		const mode = [st.pay_method_name, ...(frm.doc.payments || []).map((p) => p.mode_of_payment)]
			.filter(Boolean)
			.join(" ")
			.toLowerCase();
		for (const key in METHOD_BY_MODE_NAME) {
			if (mode.includes(key)) return METHOD_BY_MODE_NAME[key];
		}
		return MOBILE_METHODS[0];
	}

	function set_status(dlg, html) {
		dlg.fields_dict.status_html.$wrapper.html(
			`<div class="small text-muted mt-2">${html}</div>`
		);
	}

	function set_busy(dlg, busy, label) {
		const btn = dlg.get_primary_btn();
		if (!btn) return;
		btn.html(label || (busy ? __("Processing...") : __("Pay Now")));
		btn.prop("disabled", busy);
	}

	// ---------------------------------------------------------------------
	// Payment
	// ---------------------------------------------------------------------

	function pay_now(frm, st, dlg, values) {
		set_busy(dlg, true);
		set_status(dlg, __("Initiating payment..."));

		frm.dirty();
		frm
			.save()
			.then(() =>
				frappe.call({
					method: "pesepay.templates.pages.pesepay_checkout.make_seamless_payment",
					args: {
						gateway_name: st.gateway,
						amount: String(st.amount),
						currency: st.currency,
						email: "",
						phone_number: values.phone_number,
						customer_name: frm.doc.customer_name || "",
						payment_method: values.payment_method,
						reference_doctype: frm.doc.doctype,
						reference_docname: frm.doc.name,
						title: __("Payment for {0}", [frm.doc.name]),
					},
				})
			)
			.then((r) => {
				const msg = r.message || {};
				if (msg.success === false) {
					set_busy(dlg, false);
					set_status(dlg, `<span class="text-danger">${msg.error || __("Payment could not be initiated.")}</span>`);
					return;
				}
				if (msg.is_paid) {
					confirm_and_submit(frm, dlg, msg.merchant_reference);
					return;
				}
				poll_payment(frm, st, dlg, msg);
			})
			.catch(() => {
				set_busy(dlg, false);
				set_status(dlg, `<span class="text-danger">${__("Payment request failed. Please try again.")}</span>`);
			});
	}

	function resume_polling(frm, st, dlg, resume) {
		set_busy(dlg, true);
		poll_payment(frm, st, dlg, resume);
	}

	// ---------------------------------------------------------------------
	// Polling + finalize through ERPNext's own submit
	// ---------------------------------------------------------------------

	function poll_payment(frm, st, dlg, msg) {
		const ir_name = msg.merchant_reference || st.pending_ir || null;
		const poll_url = msg.poll_url || st.poll_url;
		const reference_number = msg.reference_number || (st.reference_number && !poll_url ? st.reference_number : "");
		const gateway = st.gateway;

		const max_attempts = 60;
		let attempts = 0;

		set_status(dlg, __("Waiting for the customer to approve the payment on their phone..."));

		function tick() {
			attempts += 1;
			const has_poll_url = !!poll_url;
			const args = has_poll_url
				? { poll_url: poll_url, gateway_name: gateway }
				: { reference_number: reference_number, gateway_name: gateway };
			const method = has_poll_url
				? "pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_status"
				: "pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_reference";

			frappe
				.call({ method: method, args: args })
				.then((r) => {
					const res = r.message || {};
					if (res.transactionStatus === "SUCCESS") {
						confirm_and_submit(frm, dlg, ir_name);
					} else if (res.transactionStatus === "FAILED") {
						set_busy(dlg, false);
						set_status(dlg, `<span class="text-danger">${__("Payment was declined. Please try another method.")}</span>`);
					} else if (attempts < max_attempts) {
						setTimeout(tick, 3000);
					} else {
						set_busy(dlg, false, __("Continue"));
						set_status(
							dlg,
							__("Payment is taking longer than expected. Close this popup; it will finalize when PesePay confirms.")
						);
					}
				});
		}

		setTimeout(tick, 3000);
	}

	function confirm_and_submit(frm, dlg, ir_name) {
		set_busy(dlg, true, __("Confirming..."));
		set_status(dlg, __("Confirming payment and submitting order..."));

		frappe
			.call({
				method: "pesepay.overrides.invoice.mark_pesepay_payment_confirmed",
				args: { ir_name: ir_name },
			})
			.then((r) => {
				const ok = r.message && r.message.ok;
				if (!ok) throw new Error(__("Payment confirmation failed."));

				// Hand the submit back to ERPNext's own POS flow.
				cur_pos.payment.events.submit_invoice();
				if (dlg) dlg.hide();
			})
			.catch(() => {
				set_busy(dlg, false);
				set_status(dlg, `<span class="text-danger">${__("Payment confirmed but the order could not be submitted. Please submit it manually.")}</span>`);
			});
	}

	// ---------------------------------------------------------------------
	// Event wiring
	// ---------------------------------------------------------------------

	["Sales Invoice", "POS Invoice"].forEach((doctype) => {
		frappe.ui.form.on(doctype, "after_payment_render", (frm) => mount_pay_button(frm));
	});
})();