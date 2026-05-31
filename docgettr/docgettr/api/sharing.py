"""Share links — including the guest `resolve_link` endpoint."""

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    can_share_document,
    append_audit,
)


def _hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


@frappe.whitelist()
def create_link(document, expires_at, password=None, max_views=None,
                watermark_enabled=1, recipient_label=None):
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document)
    if not can_share_document(user.name, doc):
        frappe.throw("Not authorized to share this document", frappe.PermissionError)

    link = frappe.get_doc({
        "doctype": "Docgettr Share Link",
        "document": document,
        "created_by": user.name,
        "expires_at": expires_at,
        "password_hash": _hash_password(password) if password else None,
        "watermark_enabled": int(watermark_enabled or 0),
        "max_views": int(max_views) if max_views else None,
        "view_count": 0,
        "status": "Active",
        "recipient_label": recipient_label,
    }).insert(ignore_permissions=True)

    append_audit(user.name, "ShareCreated", "Docgettr Share Link", link.name,
                 context={"document": document})
    return {"link": link.as_dict()}


@frappe.whitelist()
def list_links(document):
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document)
    if not can_share_document(user.name, doc):
        frappe.throw("Not authorized", frappe.PermissionError)
    links = frappe.get_all(
        "Docgettr Share Link",
        filters={"document": document},
        fields=["name", "token", "expires_at", "status", "watermark_enabled",
                "max_views", "view_count", "recipient_label", "created_by", "creation"],
        order_by="creation desc",
    )
    return {"links": links}


@frappe.whitelist()
def revoke_link(name):
    user = require_current_docgettr_user()
    link = frappe.get_doc("Docgettr Share Link", name)
    if link.created_by != user.name and not user.is_admin:
        frappe.throw("Not authorized", frappe.PermissionError)
    link.status = "Revoked"
    link.save(ignore_permissions=True)
    append_audit(user.name, "ShareRevoked", "Docgettr Share Link", name)
    return {"status": "ok"}


@frappe.whitelist(allow_guest=True)
def resolve_link(token, password=None):
    """Public — used by the share viewer page. Returns doc metadata + file URL
    if the token is valid (and password matches, if required)."""
    link_data = frappe.db.get_value(
        "Docgettr Share Link", {"token": token},
        ["name", "document", "created_by", "expires_at", "password_hash",
         "watermark_enabled", "max_views", "view_count", "status"],
        as_dict=True,
    )
    if not link_data:
        frappe.throw("Share link not found", frappe.DoesNotExistError)
    if link_data.status != "Active":
        frappe.throw("This share link is no longer active")
    if frappe.utils.now_datetime() > frappe.utils.get_datetime(link_data.expires_at):
        frappe.db.set_value("Docgettr Share Link", link_data.name, "status", "Expired")
        frappe.db.commit()
        frappe.throw("This share link has expired")
    if link_data.max_views and link_data.view_count >= link_data.max_views:
        frappe.throw("This share link has reached its view limit")

    if link_data.password_hash:
        if not password:
            return {"requires_password": True}
        if not _verify_password(password, link_data.password_hash):
            frappe.throw("Incorrect password", frappe.AuthenticationError)

    # Increment view count atomically
    frappe.db.sql(
        "UPDATE `tabDocgettr Share Link` SET view_count = view_count + 1 WHERE name = %s",
        link_data.name,
    )

    doc = frappe.get_doc("Docgettr Document", link_data.document)
    shared_by_name = frappe.db.get_value("Docgettr User", link_data.created_by, "display_name")

    append_audit(link_data.created_by, "ShareAccessed", "Docgettr Share Link",
                 link_data.name,
                 context={"viewer_ip": getattr(frappe.local, "request_ip", None)})

    frappe.db.commit()

    return {
        "document": {
            "name": doc.name,
            "display_filename": doc.display_filename,
            "mime_type": doc.mime_type,
            "file_url": doc.file_attachment,
            "category": doc.category,
            "document_type": doc.document_type,
        },
        "watermark_enabled": bool(link_data.watermark_enabled),
        "shared_by": shared_by_name,
        "expires_at": link_data.expires_at,
    }


@frappe.whitelist(allow_guest=True)
def download_link(token, password=None):
    """Public — stream the shared document's file for a valid token.

    Lets anyone holding the link download the underlying file (not just view a
    preview). Applies the same gate as resolve_link: the link must be Active,
    unexpired, within its view limit, and — if protected — the password must
    match. The file itself is private; we read it server-side only after the
    token has been validated, so the guest never gets direct file-ACL access.
    """
    link_data = frappe.db.get_value(
        "Docgettr Share Link", {"token": token},
        ["name", "document", "created_by", "expires_at", "password_hash",
         "max_views", "view_count", "status"],
        as_dict=True,
    )
    if not link_data:
        frappe.throw("Share link not found", frappe.DoesNotExistError)
    if link_data.status != "Active":
        frappe.throw("This share link is no longer active")
    if frappe.utils.now_datetime() > frappe.utils.get_datetime(link_data.expires_at):
        frappe.db.set_value("Docgettr Share Link", link_data.name, "status", "Expired")
        frappe.db.commit()
        frappe.throw("This share link has expired")
    if link_data.max_views and link_data.view_count >= link_data.max_views:
        frappe.throw("This share link has reached its view limit")

    if link_data.password_hash:
        if not password or not _verify_password(password, link_data.password_hash):
            frappe.throw("Incorrect password", frappe.AuthenticationError)

    doc = frappe.get_doc("Docgettr Document", link_data.document)
    if not doc.file_attachment:
        frappe.throw("No file attached to this document")

    file_doc = frappe.get_doc("File", {"file_url": doc.file_attachment})
    content = file_doc.get_content()

    append_audit(link_data.created_by, "ShareAccessed", "Docgettr Share Link",
                 link_data.name,
                 context={"viewer_ip": getattr(frappe.local, "request_ip", None),
                          "action": "download"})
    frappe.db.commit()

    frappe.local.response.filename = doc.display_filename or "document"
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
