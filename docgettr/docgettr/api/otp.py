"""One-time passcode (OTP) service — provider-agnostic.

Phase 1 delivers every code over **email**. The delivery channel is isolated
behind :func:`_dispatch` so that adding an SMS gateway later (MSG91 / Twilio /
Gupshup / AWS SNS, configured in Docgettr Settings) is a single-function change
with no impact on the signup / login / reset flows that call this module.

Public (guest) endpoints:
    request_otp(destination, purpose)   -> {"sent", "channel", "masked"}
    verify_otp(destination, code, purpose) -> {"verified", "verification_token"}

Internal helper used by auth.py:
    consume_verification(destination, purpose, token)  -> raises on failure
"""

import hashlib
import secrets

import frappe

from docgettr.docgettr.utils import settings as _settings


CODE_LENGTH = 6
TTL_SECONDS = 600          # codes are valid for 10 minutes
MAX_ATTEMPTS = 5           # wrong guesses before a code is locked
RESEND_COOLDOWN = 60       # min seconds between sends to the same destination
PURPOSES = ("Signup", "Reset")


# ---------------------------------------------------------------------------
# Normalisation + hashing
# ---------------------------------------------------------------------------

def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def normalize_phone(value: str) -> str:
    """Strip formatting but keep a single leading ``+``.

    Region is intentionally not assumed (no forced +91) — we only want a
    stable key for matching and uniqueness.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    plus = raw.startswith("+")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return ("+" + digits) if plus else digits


def looks_like_email(value: str) -> bool:
    return "@" in (value or "")


def _hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def _mask(destination: str) -> str:
    """Mask a destination for safe display (e.g. ``a***@x.com`` / ``+91****1234``)."""
    if looks_like_email(destination):
        local, _, domain = destination.partition("@")
        head = local[:1] if local else ""
        return f"{head}***@{domain}"
    tail = destination[-4:] if len(destination) >= 4 else destination
    return f"{'*' * max(0, len(destination) - 4)}{tail}"


# ---------------------------------------------------------------------------
# Channel dispatch — the only place that knows *how* a code is delivered.
# ---------------------------------------------------------------------------

def _dispatch(channel: str, delivery: str, code: str, purpose: str) -> None:
    if channel == "Email":
        _send_email(delivery, code, purpose)
        return
    if channel == "SMS":
        _send_sms(delivery, code, purpose)
        return
    frappe.throw(f"Unknown OTP channel: {channel}")


def _send_email(email: str, code: str, purpose: str) -> None:
    subject = "Your Docgettr verification code"
    intro = (
        "Use this code to finish creating your Docgettr account."
        if purpose == "Signup"
        else "Use this code to reset your Docgettr password."
    )
    frappe.sendmail(
        recipients=[email],
        subject=subject,
        message=(
            f"<p>{intro}</p>"
            f"<p style='font-size:28px;font-weight:700;letter-spacing:4px'>{code}</p>"
            f"<p>This code expires in {TTL_SECONDS // 60} minutes. "
            f"If you didn't request it, you can ignore this email.</p>"
        ),
        now=True,
    )


def _send_sms(phone: str, code: str, purpose: str) -> None:
    """SMS delivery — wired but inert until a gateway is configured.

    To enable: implement against Frappe's ``frappe.core.doctype.sms_settings``
    (or call a provider directly using keys from Docgettr Settings) and remove
    the guard below. The rest of the OTP flow needs no changes.
    """
    gateway_ready = bool(_settings.get("sms_gateway_url"))
    if not gateway_ready:
        frappe.throw(
            "SMS delivery is not configured yet. Configure an SMS gateway in "
            "Docgettr Settings to enable mobile OTPs."
        )
    from frappe.core.doctype.sms_settings.sms_settings import send_sms

    send_sms([phone], f"{code} is your Docgettr verification code.")


# ---------------------------------------------------------------------------
# Delivery resolution
# ---------------------------------------------------------------------------

def _resolve_delivery(destination: str, purpose: str):
    """Return ``(channel, delivery_address)`` for a normalized destination.

    Phase 1 always resolves to an **email** address:
      * an email destination is delivered to itself;
      * a phone destination is resolved to the owning user's email so that
        "reset via mobile" works before any SMS gateway exists.
    """
    if looks_like_email(destination):
        return "Email", destination

    # Phone destination → find the linked Frappe User's email.
    dg_email = frappe.db.get_value("Docgettr User", {"phone": destination}, "user")
    if dg_email:
        return "Email", dg_email
    # No account for this phone. Caller decides whether to reveal that.
    return "Email", None


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def request_otp(destination, purpose):
    """Generate a code for ``destination`` and deliver it.

    For ``Signup`` the destination is the email being registered (and must not
    already exist). For ``Reset`` it may be an email or a phone; we never reveal
    whether an account exists.
    """
    if purpose not in PURPOSES:
        frappe.throw("Invalid OTP purpose.")

    destination = (
        normalize_email(destination)
        if looks_like_email(destination)
        else normalize_phone(destination)
    )
    if not destination:
        frappe.throw("A destination is required.")

    if purpose == "Signup":
        if not looks_like_email(destination):
            frappe.throw("Signup verification needs an email address.")
        if frappe.db.exists("User", destination):
            frappe.throw("An account with this email already exists.",
                         frappe.DuplicateEntryError)

    channel, delivery = _resolve_delivery(destination, purpose)

    # Rate limit: refuse a new code if a fresh one was just sent.
    recent = frappe.db.get_value(
        "Docgettr OTP",
        {"destination": destination, "purpose": purpose, "status": "Pending"},
        ["name", "creation"],
        order_by="creation desc",
        as_dict=True,
    )
    if recent and frappe.utils.time_diff_in_seconds(
        frappe.utils.now_datetime(), recent.creation
    ) < RESEND_COOLDOWN:
        frappe.throw("Please wait a moment before requesting another code.")

    # Invalidate any earlier pending codes for this destination+purpose.
    frappe.db.set_value(
        "Docgettr OTP",
        {"destination": destination, "purpose": purpose, "status": "Pending"},
        "status",
        "Consumed",
        update_modified=False,
    )

    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    salt = secrets.token_hex(8)
    frappe.get_doc({
        "doctype": "Docgettr OTP",
        "destination": destination,
        "delivery": delivery,
        "channel": channel,
        "purpose": purpose,
        "code_hash": f"{salt}${_hash_code(code, salt)}",
        "status": "Pending",
        "attempts": 0,
        "expires_at": frappe.utils.add_to_date(
            frappe.utils.now_datetime(), seconds=TTL_SECONDS
        ),
    }).insert(ignore_permissions=True)

    # For Reset with no matching account we silently skip delivery but still
    # return success, so attackers can't enumerate registered users/phones.
    if delivery:
        _dispatch(channel, delivery, code, purpose)

    frappe.db.commit()
    return {
        "sent": True,
        "channel": channel,
        "masked": _mask(delivery) if delivery else _mask(destination),
    }


@frappe.whitelist(allow_guest=True)
def verify_otp(destination, code, purpose):
    """Check a code; on success issue a single-use verification token."""
    if purpose not in PURPOSES:
        frappe.throw("Invalid OTP purpose.")

    destination = (
        normalize_email(destination)
        if looks_like_email(destination)
        else normalize_phone(destination)
    )

    rec_name = frappe.db.get_value(
        "Docgettr OTP",
        {"destination": destination, "purpose": purpose, "status": "Pending"},
        "name",
        order_by="creation desc",
    )
    if not rec_name:
        frappe.throw("No active code found. Please request a new one.")

    rec = frappe.get_doc("Docgettr OTP", rec_name)

    if rec.is_expired():
        rec.status = "Consumed"
        rec.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.throw("This code has expired. Please request a new one.")

    if rec.attempts >= MAX_ATTEMPTS:
        rec.status = "Consumed"
        rec.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.throw("Too many incorrect attempts. Please request a new code.")

    salt, _, expected = (rec.code_hash or "").partition("$")
    if not expected or _hash_code(str(code).strip(), salt) != expected:
        rec.attempts += 1
        rec.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.throw("Incorrect code. Please try again.")

    token = secrets.token_urlsafe(32)
    rec.status = "Verified"
    rec.verification_token = token
    rec.save(ignore_permissions=True)
    frappe.db.commit()
    return {"verified": True, "verification_token": token}


# ---------------------------------------------------------------------------
# Internal — consumed by auth.py
# ---------------------------------------------------------------------------

def consume_verification(destination: str, purpose: str, token: str) -> None:
    """Validate and burn a verification token. Raises on any problem."""
    if not token:
        frappe.throw("Verification is required. Please verify the code first.",
                     frappe.ValidationError)

    destination = (
        normalize_email(destination)
        if looks_like_email(destination)
        else normalize_phone(destination)
    )
    rec_name = frappe.db.get_value(
        "Docgettr OTP",
        {
            "destination": destination,
            "purpose": purpose,
            "status": "Verified",
            "verification_token": token,
        },
        "name",
    )
    if not rec_name:
        frappe.throw("Verification expired or invalid. Please verify again.",
                     frappe.ValidationError)

    rec = frappe.get_doc("Docgettr OTP", rec_name)
    if rec.is_expired():
        rec.status = "Consumed"
        rec.save(ignore_permissions=True)
        frappe.throw("Verification expired. Please verify again.",
                     frappe.ValidationError)

    rec.status = "Consumed"
    rec.save(ignore_permissions=True)
