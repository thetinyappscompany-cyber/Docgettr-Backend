import json

import frappe


# ---------------------------------------------------------------------------
# Current user helpers
# ---------------------------------------------------------------------------

def get_current_docgettr_user():
    """Return the Docgettr User doc for the logged-in Frappe user, or None."""
    if frappe.session.user == "Guest":
        return None
    name = frappe.db.get_value("Docgettr User", {"user": frappe.session.user}, "name")
    if not name:
        return None
    return frappe.get_doc("Docgettr User", name)


def require_current_docgettr_user():
    user = get_current_docgettr_user()
    if not user:
        frappe.throw("Not authenticated", frappe.AuthenticationError)
    return user


def get_docgettr_user_for_frappe_user(frappe_user_email: str):
    if not frappe_user_email or frappe_user_email == "Guest":
        return None
    name = frappe.db.get_value("Docgettr User", {"user": frappe_user_email}, "name")
    return frappe.get_doc("Docgettr User", name) if name else None


# ---------------------------------------------------------------------------
# Document-level permissions
# ---------------------------------------------------------------------------

def can_read_document(user_name: str, doc) -> bool:
    if not user_name:
        return False
    if doc.uploaded_by == user_name:
        return True
    if doc.is_private:
        return False
    if doc.family:
        return bool(
            frappe.db.exists(
                "Docgettr Family Member",
                {"family": doc.family, "user": user_name},
            )
        )
    return False


def can_edit_document(user_name: str, doc) -> bool:
    if not user_name:
        return False
    if doc.uploaded_by == user_name:
        return True
    if doc.family:
        role = frappe.db.get_value(
            "Docgettr Family Member",
            {"family": doc.family, "user": user_name},
            "role",
        )
        return role in ("Admin", "Editor")
    return False


def can_share_document(user_name: str, doc) -> bool:
    if not user_name:
        return False
    if doc.uploaded_by == user_name:
        return True
    if doc.family:
        role = frappe.db.get_value(
            "Docgettr Family Member",
            {"family": doc.family, "user": user_name},
            "role",
        )
        return role == "Admin"
    return False


def can_delete_document(user_name: str, doc) -> bool:
    return can_share_document(user_name, doc)


# ---------------------------------------------------------------------------
# Frappe has_permission hooks
# ---------------------------------------------------------------------------

def document_has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    # System Manager and Administrator always pass
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return True
    if "Docgettr Admin" in frappe.get_roles(user):
        return True
    dg_user = get_docgettr_user_for_frappe_user(user)
    if not dg_user:
        return False
    if ptype in ("read", "select"):
        return can_read_document(dg_user.name, doc)
    if ptype == "write":
        return can_edit_document(dg_user.name, doc)
    if ptype == "delete":
        return can_delete_document(dg_user.name, doc)
    return False


def share_link_has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return True
    if "Docgettr Admin" in frappe.get_roles(user):
        return True
    dg_user = get_docgettr_user_for_frappe_user(user)
    if not dg_user:
        return False
    if doc.created_by == dg_user.name:
        return True
    # Allow read if user can read the underlying document
    if ptype in ("read", "select"):
        target = frappe.get_doc("Docgettr Document", doc.document)
        return can_read_document(dg_user.name, target)
    return False


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------

def append_audit(actor: str, action: str, target_kind: str = None,
                 target: str = None, context: dict = None) -> None:
    """Insert an immutable audit row. Never raises on failure."""
    try:
        frappe.get_doc({
            "doctype": "Docgettr Audit Log",
            "actor": actor,
            "action": action,
            "target_kind": target_kind,
            "target": target,
            "ts": frappe.utils.now_datetime(),
            "context_json": json.dumps(context or {}),
        }).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(message=frappe.get_traceback(), title="Audit log insert failed")
