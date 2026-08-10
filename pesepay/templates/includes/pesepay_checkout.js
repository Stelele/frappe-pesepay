	frappe.ready(function() {
	var $form = $('#pesepay-seamless-form');
	var $method = $('#payment-method');
	var $phoneFields = $('#phone-fields');
	var $cardFields = $('#card-fields');
	var $submitBtn = $('#submit-btn');
	var $error = $('#payment-error');
	var $processing = $('#payment-processing');
	var $processingMsg = $('#processing-message');

	var CARD_METHODS = ['Visa', 'MasterCard', 'Zimswitch'];
	var MOBILE_METHODS = ['EcoCash', 'InnBucks', 'Omari', 'PayGo'];

	$method.on('change', function() {
		var val = $(this).val();
		if (CARD_METHODS.indexOf(val) >= 0) {
			$phoneFields.hide();
			$cardFields.show();
			$cardFields.find('input').attr('required', true);
			$phoneFields.find('input').removeAttr('required');
		} else if (MOBILE_METHODS.indexOf(val) >= 0) {
			$cardFields.hide();
			$phoneFields.show();
			$cardFields.find('input').removeAttr('required');
			$phoneFields.find('input').attr('required', true);
		} else {
			$cardFields.hide();
			$phoneFields.show();
		}
	});

	$form.on('submit', function(e) {
		e.preventDefault();
		$error.hide();
		$submitBtn.prop('disabled', true).text('Processing...');

		var data = {};
		$form.serializeArray().forEach(function(f) { data[f.name] = f.value; });

		frappe.call({
			method: 'pesepay.templates.pages.pesepay_checkout.make_seamless_payment',
			args: data,
			freeze: false,
			callback: function(r) {
				if (!r.message) {
					showError('Unexpected response from server.');
					return;
				}

				if (!r.message.success) {
					showError(r.message.error || 'Payment failed. Please try again.');
					return;
				}

				if (r.message.is_paid) {
					window.location.href = '/payment-success';
					return;
				}

				if (r.message.redirect_url) {
					window.location.href = r.message.redirect_url;
					return;
				}

				showProcessing(r.message.poll_url, data.payment_method, r.message.reference_number, data.gateway_name);
			},
			error: function(err) {
				showError(err.status_message || 'Payment request failed.');
			}
		});
	});

	function showError(msg) {
		$error.text(msg).show();
		$submitBtn.prop('disabled', false).text('Pay Now');
	}

	function showProcessing(pollUrl, method, reference, gatewayName) {
		$form.hide();
		$processing.show();

		if (MOBILE_METHODS.indexOf(method) >= 0) {
			$processingMsg.text('Please check your phone for a payment prompt and enter your PIN.');
		}

		if (!pollUrl) {
			$processingMsg.text('Waiting for payment confirmation... Reference: ' + reference);
			return;
		}

		var attempts = 0;
		var maxAttempts = 24;

		function poll() {
			attempts++;
			frappe.call({
				method: 'pesepay.pesepay.doctype.pesepay_settings.pesepay_settings.poll_payment_status',
				args: { poll_url: pollUrl, gateway_name: gatewayName },
				freeze: false,
				callback: function(r) {
					if (r.message && r.message.transactionStatus === 'SUCCESS') {
						window.location.href = '/payment-success';
					} else if (r.message && r.message.transactionStatus === 'FAILED') {
						$processing.hide();
						$form.show();
						showError('Payment was declined. Please try another method.');
					} else if (attempts < maxAttempts) {
						setTimeout(poll, 5000);
					} else {
						$processing.hide();
						$form.show();
						showError('Payment is taking longer than expected. Please check your payment status later.');
					}
				},
				error: function() {
					if (attempts < maxAttempts) {
						setTimeout(poll, 5000);
					}
				}
			});
		}

		setTimeout(poll, 5000);
	}
});
