"""DPDP consent management."""

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    append_audit,
)


VALID_PURPOSES = {
    "AiProcessing", "OcrMetadataStorage", "Telemetry",
    "EmailTransactional", "SmsTransactional", "Marketing",
}


@frappe.whitelist()
def list_consents():
    user = require_current_docgettr_user()
    rows = frappe.get_all(
        "Docgettr Consent Record",
        filters={"user": user.name},
        fields=["*"],
        order_by="modified desc",
    )
    return {"consents": rows}


@frappe.whitelist()
def grant(purpose, notice_version=None):
    if purpose not in VALID_PURPOSES:
        frappe.throw(f"Unknown purpose: {purpose}")
    user = require_current_docgettr_user()

    existing = frappe.db.get_value(
        "Docgettr Consent Record",
        {"user": user.name, "purpose": purpose},
        "name",
    )
    if existing:
        rec = frappe.get_doc("Docgettr Consent Record", existing)
        rec.status = "Granted"
        rec.granted_at = frappe.utils.now_datetime()
        rec.revoked_at = None
        if notice_version:
            rec.notice_version = notice_version
        rec.save(ignore_permissions=True)
    else:
        rec = frappe.get_doc({
            "doctype": "Docgettr Consent Record",
            "user": user.name,
            "purpose": purpose,
            "status": "Granted",
            "granted_at": frappe.utils.now_datetime(),
            "notice_version": notice_version,
        }).insert(ignore_permissions=True)

    append_audit(user.name, "ConsentGranted", "Docgettr Consent Record", rec.name,
                 context={"purpose": purpose, "notice_version": notice_version})
    return {"consent": rec.as_dict()}


@frappe.whitelist()
def revoke(purpose):
    if purpose not in VALID_PURPOSES:
        frappe.throw(f"Unknown purpose: {purpose}")
    user = require_current_docgettr_user()

    name = frappe.db.get_value(
        "Docgettr Consent Record",
        {"user": user.name, "purpose": purpose},
        "name",
    )
    if not name:
        frappe.throw("No existing consent record to revoke")

    rec = frappe.get_doc("Docgettr Consent Record", name)
    rec.status = "Revoked"
    rec.revoked_at = frappe.utils.now_datetime()
    rec.save(ignore_permissions=True)
    append_audit(user.name, "ConsentRevoked", "Docgettr Consent Record", rec.name,
                 context={"purpose": purpose})
    return {"consent": rec.as_dict()}
