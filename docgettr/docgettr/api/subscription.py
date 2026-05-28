"""Subscription / usage endpoints."""

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    append_audit,
)
from docgettr.docgettr.utils.tier_caps import (
    get_tier_caps,
    get_document_count,
    get_storage_used_bytes,
    reset_if_new_month,
)


@frappe.whitelist()
def get_subscription():
    user = require_current_docgettr_user()
    name = frappe.db.get_value("Docgettr Subscription", {"user": user.name}, "name")
    if not name:
        return {"subscription": None}
    sub = frappe.get_doc("Docgettr Subscription", name)
    reset_if_new_month(sub)
    return {"subscription": sub.as_dict()}


@frappe.whitelist()
def get_usage():
    """Returns counts the frontend used to compute locally against IndexedDB."""
    user = require_current_docgettr_user()
    sub_name = frappe.db.get_value("Docgettr Subscription", {"user": user.name}, "name")
    sub = frappe.get_doc("Docgettr Subscription", sub_name) if sub_name else None
    if sub:
        reset_if_new_month(sub)
    caps = get_tier_caps(user.current_tier)
    addon_gb = (sub.addon_storage_gb if sub else 0) or 0
    return {
        "tier": user.current_tier,
        "document_count": get_document_count(user.name),
        "max_documents": caps["max_documents"],
        "storage_used_bytes": get_storage_used_bytes(user.name),
        "max_storage_bytes": caps["max_storage_bytes"] + addon_gb * 1024 ** 3,
        "ai_scans_used": sub.ai_scans_used_this_month if sub else 0,
        "max_ai_scans": caps["max_ai_scans"],
        "max_family_members": caps["max_family_members"],
        "max_share_expiry_hours": caps["max_share_expiry_hours"],
    }


@frappe.whitelist()
def set_tier(user_name, tier):
    """Admin-only override. Useful for testing or refunds."""
    actor = require_current_docgettr_user()
    if not actor.is_admin and "System Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Only admins can set tier directly", frappe.PermissionError)

    target = frappe.get_doc("Docgettr User", user_name)
    target.current_tier = tier
    target.save(ignore_permissions=True)
    sub_name = frappe.db.get_value("Docgettr Subscription", {"user": user_name}, "name")
    if sub_name:
        sub = frappe.get_doc("Docgettr Subscription", sub_name)
        sub.tier = tier
        sub.save(ignore_permissions=True)
    append_audit(actor.name, "TierChanged", "Docgettr Subscription", sub_name,
                 context={"new_tier": tier, "method": "admin_override",
                          "target_user": user_name})
    return {"status": "ok", "tier": tier}
