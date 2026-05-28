"""Smart folder CRUD."""

import json

import frappe

from docgettr.docgettr.utils.permissions import require_current_docgettr_user


@frappe.whitelist()
def list_folders():
    user = require_current_docgettr_user()
    rows = frappe.get_all(
        "Docgettr Smart Folder",
        filters={"user": user.name},
        fields=["*"],
        order_by="display_order asc",
    )
    return {"smart_folders": rows}


@frappe.whitelist()
def upsert(label, filter_json, name=None, icon_lucide=None,
           display_order=0, is_system=0):
    user = require_current_docgettr_user()
    if not isinstance(filter_json, str):
        filter_json = json.dumps(filter_json or {})

    if name:
        folder = frappe.get_doc("Docgettr Smart Folder", name)
        if folder.user != user.name:
            frappe.throw("Not authorized", frappe.PermissionError)
        folder.label = label
        folder.filter_json = filter_json
        folder.icon_lucide = icon_lucide
        folder.display_order = int(display_order or 0)
        folder.is_system = int(is_system or 0)
        folder.save(ignore_permissions=True)
    else:
        folder = frappe.get_doc({
            "doctype": "Docgettr Smart Folder",
            "user": user.name,
            "label": label,
            "icon_lucide": icon_lucide,
            "filter_json": filter_json,
            "display_order": int(display_order or 0),
            "is_system": int(is_system or 0),
        }).insert(ignore_permissions=True)
    return {"smart_folder": folder.as_dict()}


@frappe.whitelist()
def delete(name):
    user = require_current_docgettr_user()
    folder = frappe.get_doc("Docgettr Smart Folder", name)
    if folder.user != user.name:
        frappe.throw("Not authorized", frappe.PermissionError)
    if folder.is_system:
        frappe.throw("Cannot delete a system smart folder")
    frappe.delete_doc("Docgettr Smart Folder", name, ignore_permissions=True)
    return {"status": "ok"}
