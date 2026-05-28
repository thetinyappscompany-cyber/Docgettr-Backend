"""Razorpay-backed payment flow."""

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    append_audit,
)


PRICING = {
    ("PremiumIndividual", "Monthly"): 4900,
    ("PremiumIndividual", "Annual"): 29900,
    ("PremiumFamily", "Monthly"): 9900,
    ("PremiumFamily", "Annual"): 59900,
}


def _razorpay_client():
    import razorpay
    key_id = frappe.conf.get("razorpay_key_id")
    key_secret = frappe.conf.get("razorpay_key_secret")
    if not key_id or not key_secret:
        frappe.throw("Razorpay is not configured on the server.")
    return razorpay.Client(auth=(key_id, key_secret))


def _calculate_period_end(cycle: str):
    if cycle == "Monthly":
        return frappe.utils.add_months(frappe.utils.now_datetime(), 1)
    if cycle == "Annual":
        return frappe.utils.add_to_date(frappe.utils.now_datetime(), years=1)
    return None


@frappe.whitelist()
def create_order(tier, billing_cycle):
    user = require_current_docgettr_user()
    amount = PRICING.get((tier, billing_cycle))
    if not amount:
        frappe.throw("Invalid tier/cycle combination")

    client = _razorpay_client()
    order = client.order.create({
        "amount": amount,
        "currency": "INR",
        "receipt": f"docgettr_{frappe.generate_hash(length=10)}",
        "notes": {
            "tier": tier,
            "cycle": billing_cycle,
            "docgettr_user": user.name,
        },
    })
    return {
        "order_id": order["id"],
        "amount": amount,
        "currency": "INR",
        "key_id": frappe.conf.get("razorpay_key_id"),
    }


@frappe.whitelist()
def verify_payment(razorpay_order_id, razorpay_payment_id, razorpay_signature):
    user = require_current_docgettr_user()
    client = _razorpay_client()

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature,
        })
    except Exception:
        frappe.throw("Payment signature verification failed", frappe.AuthenticationError)

    order = client.order.fetch(razorpay_order_id)
    notes = order.get("notes") or {}
    tier = notes.get("tier")
    cycle = notes.get("cycle")
    if not tier or not cycle:
        frappe.throw("Order is missing tier/cycle metadata")

    # Upgrade
    user.current_tier = tier
    user.save(ignore_permissions=True)

    sub_name = frappe.db.get_value("Docgettr Subscription", {"user": user.name}, "name")
    sub = frappe.get_doc("Docgettr Subscription", sub_name)
    sub.tier = tier
    sub.billing_cycle = cycle
    sub.razorpay_order_id = razorpay_order_id
    sub.current_period_end = _calculate_period_end(cycle)
    sub.status = "Active"
    sub.save(ignore_permissions=True)

    append_audit(user.name, "TierChanged", "Docgettr Subscription", sub.name,
                 context={"new_tier": tier, "cycle": cycle,
                          "razorpay_payment_id": razorpay_payment_id})
    return {"status": "ok", "tier": tier, "subscription": sub.as_dict()}


@frappe.whitelist(allow_guest=True)
def razorpay_webhook():
    """Optional — Razorpay can POST webhooks here for renewals, failures, etc."""
    import hmac
    import hashlib

    body = frappe.request.get_data(as_text=True)
    secret = frappe.conf.get("razorpay_webhook_secret")
    if not secret:
        frappe.throw("Webhook secret not configured", frappe.AuthenticationError)

    signature = frappe.get_request_header("X-Razorpay-Signature")
    expected = hmac.new(
        secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        frappe.throw("Invalid webhook signature", frappe.AuthenticationError)

    import json as _json
    payload = _json.loads(body)
    event = payload.get("event")

    # Future: handle subscription.charged, payment.failed, etc.
    frappe.log_error(
        message=f"Razorpay webhook received: {event}",
        title="Razorpay webhook",
    )
    return {"status": "received"}
