"""Reminder CRUD."""

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    can_read_document,
    append_audit,
)


@frappe.whitelist()
def list_reminders(status=None):
    user = require_current_docgettr_user()
    filters = {"owner_user": user.name}
    if status:
        filters["status"] = status
    rows = frappe.get_all(
        "Docgettr Reminder",
        filters=filters,
        fields=["*"],
        order_by="fire_at asc",
    )
    return {"reminders": rows}


@frappe.whitelist()
def upsert(document, kind, fire_at, title, body=None, offset_days=None,
           channel_email=1, channel_push=1, channel_sms=0, name=None):
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document)
    if not can_read_document(user.name, doc):
        frappe.throw("Not authorized", frappe.PermissionError)

    if name:
        rem = frappe.get_doc("Docgettr Reminder", name)
        if rem.owner_user != user.name:
            frappe.throw("Not authorized", frappe.PermissionError)
        rem.document = document
        rem.kind = kind
        rem.fire_at = fire_at
        rem.title = title
        rem.body = body
        rem.offset_days = offset_days
        rem.channel_email = int(channel_email or 0)
        rem.channel_push = int(channel_push or 0)
        rem.channel_sms = int(channel_sms or 0)
        rem.save(ignore_permissions=True)
    else:
        rem = frappe.get_doc({
            "doctype": "Docgettr Reminder",
            "document": document,
            "owner_user": user.name,
            "kind": kind,
            "fire_at": fire_at,
            "title": title,
            "body": body,
            "offset_days": offset_days,
            "channel_email": int(channel_email or 0),
            "channel_push": int(channel_push or 0),
            "channel_sms": int(channel_sms or 0),
            "status": "Pending",
        }).insert(ignore_permissions=True)

    return {"reminder": rem.as_dict()}


@frappe.whitelist()
def snooze(name, until):
    user = require_current_docgettr_user()
    rem = frappe.get_doc("Docgettr Reminder", name)
    if rem.owner_user != user.name:
        frappe.throw("Not authorized", frappe.PermissionError)
    rem.status = "Snoozed"
    rem.snoozed_until = until
    rem.fire_at = until
    rem.save(ignore_permissions=True)
    append_audit(user.name, "ReminderSnoozed", "Docgettr Reminder", name)
    return {"reminder": rem.as_dict()}


@frappe.whitelist()
def complete(name):
    user = require_current_docgettr_user()
    rem = frappe.get_doc("Docgettr Reminder", name)
    if rem.owner_user != user.name:
        frappe.throw("Not authorized", frappe.PermissionError)
    rem.status = "Completed"
    rem.save(ignore_permissions=True)
    append_audit(user.name, "ReminderCompleted", "Docgettr Reminder", name)
    return {"reminder": rem.as_dict()}


@frappe.whitelist()
def delete_for_document(document):
    user = require_current_docgettr_user()
    rows = frappe.get_all(
        "Docgettr Reminder",
        filters={"document": document, "owner_user": user.name},
        pluck="name",
    )
    for rid in rows:
        frappe.delete_doc("Docgettr Reminder", rid, ignore_permissions=True)
    return {"deleted": len(rows)}
