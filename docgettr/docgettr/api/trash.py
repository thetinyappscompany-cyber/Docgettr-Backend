"""Trash listing."""

import frappe

from docgettr.docgettr.utils.permissions import require_current_docgettr_user


@frappe.whitelist()
def list_trash():
    user = require_current_docgettr_user()
    items = frappe.get_all(
        "Docgettr Trash Item",
        filters={"deleted_by": user.name},
        fields=["name", "original_doc", "deleted_ts", "auto_purge_at", "reason"],
        order_by="deleted_ts desc",
    )
    # Enrich each item with the underlying document's display info
    for item in items:
        meta = frappe.db.get_value(
            "Docgettr Document",
            item["original_doc"],
            ["display_filename", "document_type", "category", "mime_type"],
            as_dict=True,
        ) or {}
        item.update(meta)
    return {"trash_items": items}
