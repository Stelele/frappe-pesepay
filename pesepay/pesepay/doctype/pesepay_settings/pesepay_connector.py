from __future__ import annotations

import json
import urllib.parse
from typing import Any, Optional

import frappe
from frappe.utils import get_request_session

# ---------------------------------------------------------------------------
# cryptography.hazmat  --  AES-256-CBC with PKCS7 padding
# ---------------------------------------------------------------------------
import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


class PesePayException(Exception):
    """Raised for PesePay-specific errors (config missing, HTTP errors,
    unsupported payment method, etc.)."""
    pass


# ---------------------------------------------------------------------------
# Static method-code lookup table
# ---------------------------------------------------------------------------
METHOD_CODES: dict[tuple[str, str], str] = {
    ("EcoCash", "USD"): "PZW211",
    ("EcoCash", "ZiG"): "PZW201",
    ("InnBucks", "USD"): "PZW212",
    ("Visa", "USD"): "PZW204",
    ("MasterCard", "USD"): "PZW205",
    ("Zimswitch", "USD"): "PZW215",
    ("Omari", "USD"): "PZW216",
    ("PayGo", "ZiG"): "PZW210",
}


# ---------------------------------------------------------------------------
# Custom JSON encoder that mirrors the C# JsonSerializerOptions used in the
# PesePay SDK:
#   - camelCase property names
#   - null fields excluded (DefaultIgnoreCondition.WhenWritingNull)
#   - Amount struct uses [JsonPropertyName("amount")] / [JsonPropertyName("currencyCode")]
# ---------------------------------------------------------------------------

def _dumps_camel_case(obj: Any) -> str:
    """Serialize *obj* to a JSON string that mirrors the C# SDK output:

    * camelCase field names (transformed from snake_case if the keys are
      recognised mapping names, otherwise passed through).
    * ``null`` fields are stripped.
    * The top-level envelope keys (``"payload"``) are NOT lowercased
      because they are already the exact wire format expected by PesePay.
    """
    return json.dumps(
        _camel_case(obj),
        separators=(",", ":"),
        default=str,
    )


_SNAKE_TO_CAMEL_OVERRIDES: dict[str, str] = {
    "amount_details": "amountDetails",
    "reason_for_payment": "reasonForPayment",
    "merchant_reference": "merchantReference",
    "result_url": "resultUrl",
    "return_url": "returnUrl",
    "reference_number": "referenceNumber",
    "poll_url": "pollUrl",
    "redirect_url": "redirectUrl",
    "internal_reference": "internalReference",
    "transaction_status": "transactionStatus",
    "transaction_status_code": "transactionStatusCode",
    "transaction_status_description": "transactionStatusDescription",
    "payment_method_code": "paymentMethodCode",
    "currency_code": "currencyCode",
    "phone_number": "phoneNumber",
    "customer_name": "name",
    "card_details": "cardDetails",
    "payment_method_required_fields": "paymentMethodRequiredFields",
    "payment_request_fields": "paymentRequestFields",
    "payment_method": "paymentMethod",
    "is_paid": "isPaid",
    "integration_key": "integrationKey",
    "encryption_key": "encryptionKey",
    "use_sandbox": "useSandbox",
}

# The Amount sub-object uses the PascalCase names from the C# struct:
#   public decimal Amount { get; set; }        → "amount"
#   public string CurrencyCode { get; set; }   → "currencyCode"
_AMOUNT_FIELDS = {"value": "amount", "currency": "currencyCode"}


def _camel_case(obj: Any) -> Any:
    """Recursively convert dict keys to camelCase and drop ``None`` values."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if v is None:
                continue
            new_key = _SNAKE_TO_CAMEL_OVERRIDES.get(k, k)
            # Also check the amount-specific renames (lower-level alias)
            new_key = _AMOUNT_FIELDS.get(new_key, new_key)
            out[new_key] = _camel_case(v)
        return out
    if isinstance(obj, list):
        return [_camel_case(i) for i in obj]
    return obj


# ---------------------------------------------------------------------------
# AES-256-CBC crypto helper  (port of C# AesCbcPayloadCrypto)
# ---------------------------------------------------------------------------

class _AesCbcPayloadCrypto:
    """Port of the C# ``AesCbcPayloadCrypto`` class.

    Encryption key (string) → UTF-8 bytes for AES key (MUST be 32 bytes).
    IV = first **16 characters** of the key string, UTF-8 encoded
    (NOT the first 16 bytes of the key bytes — matches C# behaviour).
    """

    def __init__(self, encryption_key: str):
        key_bytes = encryption_key.encode("utf-8")
        if len(key_bytes) != 32:
            raise PesePayException(
                f"Encryption key must produce exactly 32 UTF-8 bytes, "
                f"got {len(key_bytes)}."
            )
        # IV = first 16 characters of the *string*, UTF-8 encoded
        iv_bytes = encryption_key[:16].encode("utf-8")
        if len(iv_bytes) != 16:
            raise PesePayException(
                f"First 16 characters of the encryption key must produce "
                f"exactly 16 UTF-8 IV bytes, got {len(iv_bytes)}."
            )
        self._cipher = Cipher(algorithms.AES256(key_bytes), modes.CBC(iv_bytes))
        self._padder = PKCS7(128)

    # ---- public API -------------------------------------------------------

    def encrypt(self, plaintext: bytes) -> str:
        """Return base64-encoded ciphertext."""
        encryptor = self._cipher.encryptor()
        padder = self._padder.padder()
        padded = padder.update(plaintext) + padder.finalize()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode("ascii")

    def decrypt(self, ciphertext_b64: str) -> bytes:
        """Decrypt a base64-encoded ciphertext and return plaintext bytes."""
        raw = base64.b64decode(ciphertext_b64)
        decryptor = self._cipher.decryptor()
        padded = decryptor.update(raw) + decryptor.finalize()
        unpadder = self._padder.unpadder()
        return unpadder.update(padded) + unpadder.finalize()


# ---------------------------------------------------------------------------
# PesePayConnector
# ---------------------------------------------------------------------------

class PesePayConnector:
    """HTTP + crypto client for the PesePay Payments Engine API.

    Port of the C# SDK's ``PesePayConnector`` / ``PesePayClient``.
    """

    PROD_BASE_URL = "https://api.pesepay.com/api/payments-engine/"
    SANDBOX_BASE_URL = "https://api.test.sandbox.pesepay.com/payments-engine/"

    def __init__(
        self,
        integration_key: str,
        encryption_key: str,
        use_sandbox: bool = True,
        timeout: int = 30,
    ):
        self._integration_key = integration_key
        self._encryption_key = encryption_key
        self._crypto = _AesCbcPayloadCrypto(encryption_key)
        self._base_url = self.SANDBOX_BASE_URL if use_sandbox else self.PROD_BASE_URL
        self._timeout = timeout

    # ── public API methods ────────────────────────────────────────────────

    def initiate_redirect_payment(
        self,
        amount: float,
        currency: str,
        reason: str,
        merchant_ref: str,
        result_url: str,
        return_url: str,
    ) -> dict[str, Any]:
        """POST ``v1/payments/initiate`` — redirect-based payment."""
        transaction = {
            "amount_details": {"value": amount, "currency": currency},
            "reason_for_payment": reason,
            "merchant_reference": merchant_ref,
            "result_url": result_url,
            "return_url": return_url,
        }
        payload = self._encrypt(transaction)
        raw = self._post("v1/payments/initiate", payload)
        return self._decrypt_response(raw)

    def initiate_seamless_payment(
        self,
        method: str,
        currency: str,
        amount: float,
        reason: str,
        merchant_ref: str,
        email: str,
        phone: str,
        customer_name: str,
        card_details: Optional[dict[str, str]] = None,
        result_url: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST ``v2/payments/make-payment`` — seamless / inline payment."""
        method_code = self._resolve_method_code(method, currency)

        fields: dict[str, Any] = {}
        if card_details:
            fields["creditCardNumber"] = card_details.get("number", "")
            fields["creditCardSecurityNumber"] = card_details.get("cvv", "")
            fields["creditCardExpiryDate"] = card_details.get("expiry", "")
            holder = card_details.get("holder", "")
            if holder:
                fields["creditCardHolder"] = holder
        elif phone:
            fields["customerPhoneNumber"] = phone
        else:
            raise PesePayException(
                "Seamless payment requires either a Card or PhoneNumber."
            )

        if not result_url:
            raise PesePayException("Result URL has not been specified.")

        payment = {
            "currency_code": currency,
            "payment_method_code": method_code,
            "customer": {
                "email": email,
                "phone_number": phone,
                "customer_name": customer_name,
            },
            "amount_details": {"value": amount, "currency": currency},
            "reason_for_payment": reason,
            "merchant_reference": merchant_ref,
            "payment_method_required_fields": fields,
            "payment_request_fields": fields,
            "result_url": result_url,
        }
        if return_url:
            payment["return_url"] = return_url

        payload = self._encrypt(payment)
        raw = self._post("v2/payments/make-payment", payload)
        return self._decrypt_response(raw)

    def check_payment_status(self, reference_number: str) -> dict[str, Any]:
        """GET ``v1/payments/check-payment`` by reference number."""
        url_suffix = (
            f"v1/payments/check-payment"
            f"?referenceNumber={urllib.parse.quote(reference_number, safe='')}"
        )
        raw = self._get(url_suffix)
        return self._decrypt_response(raw)

    def poll_payment(self, poll_url: str) -> dict[str, Any]:
        """GET an arbitrary (absolute or relative) poll URL."""
        if poll_url.startswith("http://") or poll_url.startswith("https://"):
            url = poll_url
        else:
            # relative path — resolve against base URL
            url = urllib.parse.urljoin(self._base_url, poll_url)
        raw = self._get_url(url)
        return self._decrypt_response(raw)

    # ── internal helpers ──────────────────────────────────────────────────

    def _encrypt(self, data: dict[str, Any]) -> dict[str, str]:
        """Encode *data* as camelCase JSON → AES-256-CBC → base64 envelope."""
        json_str = _dumps_camel_case(data)
        b64_cipher = self._crypto.encrypt(json_str.encode("utf-8"))
        return {"payload": b64_cipher}

    def _decrypt_response(self, raw_body: dict[str, Any]) -> dict[str, Any]:
        """Decrypt the ``"payload"`` field from *raw_body*.

        If decryption fails (e.g. the response is not actually encrypted, as
        is the case for the ``currencies`` / ``payment-methods`` endpoints),
        return the raw body unchanged.
        """
        payload_b64 = raw_body.get("payload")
        if not payload_b64 or not isinstance(payload_b64, str):
            return raw_body

        try:
            plain_bytes = self._crypto.decrypt(payload_b64)
            return json.loads(plain_bytes.decode("utf-8"))
        except (ValueError, base64.binascii.Error) as exc:
            # C# SDK catches CryptographicException and falls back to raw body
            frappe.log_error(
                title="PesePay Decrypt Fallback",
                message=f"Decryption failed, returning raw body. Error: {exc}",
            )
            return raw_body

    def _post(
        self, path: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """POST to *path* (relative to ``_base_url``) with envelope JSON."""
        url = urllib.parse.urljoin(self._base_url, path)
        try:
            session = get_request_session()
            response = session.post(
                url,
                headers=self._headers(),
                data=json.dumps(data, separators=(",", ":"), default=str),
                timeout=self._timeout,
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("application/") and content_type.split(";")[0].endswith("json"):
                return response.json()
            return {}
        except Exception as exc:
            raise PesePayException(f"POST {url} failed: {exc}") from exc

    def _get(self, path: str) -> dict[str, Any]:
        """GET a path relative to ``_base_url``."""
        url = urllib.parse.urljoin(self._base_url, path)
        return self._get_url(url)

    def _get_url(self, url: str) -> dict[str, Any]:
        """GET an arbitrary absolute URL with auth headers."""
        try:
            session = get_request_session()
            response = session.get(
                url,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("application/") and content_type.split(";")[0].endswith("json"):
                return response.json()
            return {}
        except Exception as exc:
            raise PesePayException(f"GET {url} failed: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "authorization": self._integration_key,  # NO "Bearer" prefix
        }

    @staticmethod
    def _resolve_method_code(method: str, currency: str) -> str:
        """Look up the PesePay method code for a (payment_method, currency) pair."""
        code = METHOD_CODES.get((method, currency))
        if not code:
            raise PesePayException(
                f"Unsupported payment method / currency combination: "
                f"'{method}' / '{currency}'. "
                f"Supported combos: {', '.join(f'{m} ({c})' for (m, c) in METHOD_CODES)}"
            )
        return code
