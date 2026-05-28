"""Access request flow — non-owners request access, owner approves/denies."""

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    can_read_document,
    append_audit,
)


@frappe.whitelist()
def create(document, note=None):
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document)
    if can_read_document(user.name, doc):
        frappe.throw("You already have access to this document")
    if frappe.db.exists("Docgettr Access Request", {
        "requester": user.name, "document": document, "status": "Pending"
    }):
        frappe.throw("You already have a pending request for this document")
    req = frappe.get_doc({
        "doctype": "Docgettr Access Request",
        "requester": user.name,
        "document": document,
        "status": "Pending",
        "note": note,
    }).insert(ignore_permissions=True)
    append_audit(user.name, "PermissionRequested", "Docgettr Access Request", req.name,
                 context={"document": document})
    return {"request": req.as_dict()}


@frappe.whitelist()
def list_requests(role="owner"):
    """role: 'owner' returns requests against documents this user owns;
    'requester' returns requests this user has made."""
    user = require_current_docgettr_user()
    if role == "requester":
        rows = frappe.get_all(
            "Docgettr Access Request",
            filters={"requester": user.name},
            fields=["*"],
            order_by="creation desc",
        )
    else:
        owned_docs = frappe.get_all(
            "Docgettr Document",
            filters={"uploaded_by": user.name},
            pluck="name",
        )
        rows = (
            frappe.get_all(
                "Docgettr Access Request",
                filters={"document": ["in", owned_docs]},
                fields=["*"],
                order_by="creation desc",
            )
            if owned_docs
            else []
        )
    return {"requests": rows}


@frappe.whitelist()
def resolve(name, decision, note=None):
    if decision not in ("Approved", "Denied"):
        frappe.throw("decision must be 'Approved' or 'Denied'")
    user = require_current_docgettr_user()
    req = frappe.get_doc("Docgettr Access Request", name)
    doc = frappe.get_doc("Docgettr Document", req.document)
    if doc.uploaded_by != user.name and not user.is_admin:
        frappe.throw("Not authorized to resolve this request", frappe.PermissionError)

    req.status = decision
    req.resolved_at = frappe.utils.now_datetime()
    req.resolved_by = user.name
    if note is not None:
        req.note = note
    req.save(ignore_permissions=True)

    action = "PermissionGranted" if decision == "Approved" else "PermissionDenied"
    append_audit(user.name, action, "Docgettr Access Request", name,
                 context={"document": req.document, "requester": req.requester})

    # On approval, the simplest approach is to add the requester as a Viewer
    # to the document's family (if any). For personal docs we just mark
    # the request approved — UI can surface this via a shared-with-me view.
    if decision == "Approved" and doc.family:
        if not frappe.db.exists("Docgettr Family Member", {
            "family": doc.family, "user": req.requester,
        }):
            frappe.get_doc({
                "doctype": "Docgettr Family Member",
                "family": doc.family,
                "user": req.requester,
                "kind": "Linked",
                "role": "Viewer",
                "display_name": frappe.db.get_value(
                    "Docgettr User", req.requester, "display_name"
                ) or req.requester,
            }).insert(ignore_permissions=True)

    return {"request": req.as_dict()}
