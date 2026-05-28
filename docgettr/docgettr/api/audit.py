"""Audit log queries."""

import frappe

from docgettr.docgettr.utils.permissions import require_current_docgettr_user


@frappe.whitelist()
def list_audit(action=None, target=None, page=1, page_size=50,
               from_ts=None, to_ts=None):
    user = require_current_docgettr_user()
    filters = {"actor": user.name}
    if action:
        filters["action"] = action
    if target:
        filters["target"] = target
    if from_ts:
        filters["ts"] = [">=", from_ts]
    if to_ts:
        filters.setdefault("ts", [])
        # Frappe filter syntax doesn't compose two on the same key easily — use SQL fallback
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 1), 200)
    offset = (page - 1) * page_size

    rows = frappe.get_all(
        "Docgettr Audit Log",
        filters=filters,
        fields=["name", "actor", "action", "target_kind", "target", "ts", "context_json"],
        order_by="ts desc",
        start=offset,
        limit_page_length=page_size,
    )
    total = frappe.db.count("Docgettr Audit Log", filters=filters)
    return {"audit": rows, "total": total, "page": page, "page_size": page_size}
