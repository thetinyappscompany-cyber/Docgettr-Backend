"""Document CRUD endpoints."""

import json
import mimetypes

import frappe
from frappe.utils.file_manager import save_file, get_file

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    can_read_document,
    can_edit_document,
    can_delete_document,
    append_audit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_uploaded_file_bytes():
    """Pull the bytes of the file uploaded with the current request, if any."""
    files = frappe.request.files if getattr(frappe, "request", None) else None
    if not files:
        return None, None, None
    fileobj = files.get("file")
    if not fileobj:
        return None, None, None
    content = fileobj.stream.read()
    return content, fileobj.filename, fileobj.mimetype


def _guess_mime(file_doc, fallback_name=None) -> str:
    """Best-effort MIME type for a Frappe File (which has no content_type field)."""
    name = getattr(file_doc, "file_name", None) or file_doc.file_url or fallback_name
    return (mimetypes.guess_type(name)[0] if name else None) or "application/octet-stream"


def _serialize(doc) -> dict:
    d = doc.as_dict()
    # Deserialize JSON fields for frontend convenience
    for k in ("ai_extracted_fields_json", "ai_confidence_per_field_json", "tags_json"):
        if d.get(k) and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except (ValueError, TypeError):
                pass
    return d


def _resolve_doc_for_user(name):
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", name)
    if not can_read_document(user.name, doc):
        frappe.throw("Not authorized to access this document", frappe.PermissionError)
    return user, doc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def upload(document_type=None, category=None, family=None, belongs_to_member=None,
           display_filename=None, tags=None, notes=None, physical_location=None,
           is_private=0):
    """Upload a document. Expects a multipart `file` field on the request."""
    user = require_current_docgettr_user()

    content, fname, mime = _get_uploaded_file_bytes()
    if not content:
        frappe.throw("No file uploaded. Provide multipart form field 'file'.")

    # Save file via Frappe's File DocType (private storage)
    file_doc = save_file(
        fname=display_filename or fname,
        content=content,
        dt="Docgettr Document",
        dn=None,
        is_private=1,
    )

    doc = frappe.get_doc({
        "doctype": "Docgettr Document",
        "uploaded_by": user.name,
        "document_type": document_type or None,
        "category": category or None,
        "family": family or None,
        "belongs_to_member": belongs_to_member or None,
        "display_filename": display_filename or fname,
        "original_filename": fname,
        "file_attachment": file_doc.file_url,
        "mime_type": mime or _guess_mime(file_doc, fname),
        "file_size_bytes": len(content),
        "storage_backend": user.storage_backend or "CloudVault",
        "tags_json": tags or "[]",
        "notes": notes,
        "physical_location": physical_location,
        "is_private": int(is_private or 0),
        "status": "Active",
        "version_no": 1,
    }).insert(ignore_permissions=True)

    # Re-parent the file row to the new document name
    frappe.db.set_value("File", file_doc.name, "attached_to_name", doc.name)

    return {"document": _serialize(doc)}


@frappe.whitelist()
def get(name):
    _, doc = _resolve_doc_for_user(name)
    return {"document": _serialize(doc)}


@frappe.whitelist()
def query(filters=None, search=None, sort_by="modified", sort_order="desc",
          page=1, page_size=20):
    """Filter + free-text-search the user's accessible documents."""
    user = require_current_docgettr_user()

    if isinstance(filters, str):
        filters = json.loads(filters or "{}")
    filters = filters or {}

    # Build the WHERE clauses
    accessible_families = frappe.get_all(
        "Docgettr Family Member",
        filters={"user": user.name},
        pluck="family",
    )

    where = ["(d.uploaded_by = %(user)s"]
    params = {"user": user.name}
    if accessible_families:
        where[0] += " OR (d.family IN %(families)s AND d.is_private = 0)"
        params["families"] = tuple(accessible_families)
    where[0] += ")"

    # Standard filters
    for key in ("status", "category", "document_type", "family", "belongs_to_member"):
        if filters.get(key):
            where.append(f"d.{key} = %({key})s")
            params[key] = filters[key]
    if "is_favorite" in filters:
        where.append("d.is_favorite = %(is_favorite)s")
        params["is_favorite"] = int(filters["is_favorite"])
    if "is_archived" in filters:
        where.append("d.is_archived = %(is_archived)s")
        params["is_archived"] = int(filters["is_archived"])

    # Default status filter — exclude trashed unless explicitly asked
    if "status" not in filters:
        where.append("d.status = 'Active'")

    # Free-text search
    if search:
        where.append("""(
            d.display_filename LIKE %(q)s
            OR d.ocr_text LIKE %(q)s
            OR d.ai_extracted_fields_json LIKE %(q)s
            OR d.tags_json LIKE %(q)s
            OR d.notes LIKE %(q)s
        )""")
        params["q"] = f"%{search}%"

    # Whitelist sort fields to prevent injection
    sort_by_safe = sort_by if sort_by in (
        "modified", "creation", "display_filename", "expiry_date",
        "issue_date", "ai_confidence_overall", "file_size_bytes",
    ) else "modified"
    sort_order_safe = "ASC" if str(sort_order).lower() == "asc" else "DESC"

    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    offset = (page - 1) * page_size

    where_sql = " AND ".join(where)

    total = frappe.db.sql(
        f"SELECT COUNT(*) FROM `tabDocgettr Document` d WHERE {where_sql}",
        params,
    )[0][0]

    rows = frappe.db.sql(
        f"""
        SELECT d.name FROM `tabDocgettr Document` d
        WHERE {where_sql}
        ORDER BY d.{sort_by_safe} {sort_order_safe}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": page_size, "offset": offset},
        as_dict=True,
    )

    docs = [
        _serialize(frappe.get_doc("Docgettr Document", r["name"]))
        for r in rows
    ]

    return {
        "documents": docs,
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


@frappe.whitelist()
def update(name, patch):
    """Update document metadata fields."""
    user, doc = _resolve_doc_for_user(name)
    if not can_edit_document(user.name, doc):
        frappe.throw("Not authorized to edit this document", frappe.PermissionError)

    if isinstance(patch, str):
        patch = json.loads(patch or "{}")

    editable = {
        "display_filename", "document_type", "category", "family", "belongs_to_member",
        "tags_json", "issue_date", "expiry_date", "is_favorite", "is_archived",
        "is_private", "notes", "physical_location", "is_checked_out", "checked_out_to",
        "ai_extracted_fields_json", "ocr_text",
    }
    changed = False
    for k, v in (patch or {}).items():
        if k in editable:
            if k in ("tags_json", "ai_extracted_fields_json") and not isinstance(v, str):
                v = json.dumps(v)
            setattr(doc, k, v)
            changed = True

    if changed:
        doc.save(ignore_permissions=True)
        append_audit(user.name, "DocumentEdited", "Docgettr Document", name)

    return {"document": _serialize(doc)}


@frappe.whitelist()
def replace_file(name):
    """Upload a new file as a new version of an existing document."""
    user, doc = _resolve_doc_for_user(name)
    if not can_edit_document(user.name, doc):
        frappe.throw("Not authorized to replace this file", frappe.PermissionError)

    content, fname, mime = _get_uploaded_file_bytes()
    if not content:
        frappe.throw("No file uploaded.")

    # Archive previous version
    frappe.get_doc({
        "doctype": "Docgettr Document Version",
        "document": doc.name,
        "version_no": doc.version_no,
        "display_filename": doc.display_filename,
        "mime_type": doc.mime_type,
        "file_size_bytes": doc.file_size_bytes,
        "file_attachment": doc.file_attachment,
        "replaced_at": frappe.utils.now_datetime(),
        "replaced_by": user.name,
    }).insert(ignore_permissions=True)

    # Save new file
    file_doc = save_file(
        fname=fname,
        content=content,
        dt="Docgettr Document",
        dn=doc.name,
        is_private=1,
    )

    doc.file_attachment = file_doc.file_url
    doc.mime_type = mime or _guess_mime(file_doc, fname)
    doc.file_size_bytes = len(content)
    doc.original_filename = fname
    doc.version_no = (doc.version_no or 1) + 1
    doc.save(ignore_permissions=True)

    append_audit(user.name, "DocumentReplaced", "Docgettr Document", name)
    return {"document": _serialize(doc)}


@frappe.whitelist()
def soft_delete(name):
    user, doc = _resolve_doc_for_user(name)
    if not can_delete_document(user.name, doc):
        frappe.throw("Not authorized to delete this document", frappe.PermissionError)
    doc.status = "Trashed"
    doc.save(ignore_permissions=True)
    return {"status": "ok"}


@frappe.whitelist()
def restore(name):
    user, doc = _resolve_doc_for_user(name)
    if not can_edit_document(user.name, doc):
        frappe.throw("Not authorized to restore this document", frappe.PermissionError)
    doc.status = "Active"
    doc.save(ignore_permissions=True)
    # Remove from trash
    trash = frappe.db.get_value("Docgettr Trash Item", {"original_doc": name}, "name")
    if trash:
        frappe.delete_doc("Docgettr Trash Item", trash, ignore_permissions=True)
    return {"status": "ok"}


@frappe.whitelist()
def permanent_delete(name):
    user, doc = _resolve_doc_for_user(name)
    if not can_delete_document(user.name, doc):
        frappe.throw("Not authorized to delete this document", frappe.PermissionError)
    # Delete the file too
    if doc.file_attachment:
        try:
            file_name = frappe.db.get_value("File", {"file_url": doc.file_attachment}, "name")
            if file_name:
                frappe.delete_doc("File", file_name, ignore_permissions=True)
        except Exception:
            pass
    # Also remove any trash item
    trash = frappe.db.get_value("Docgettr Trash Item", {"original_doc": name}, "name")
    if trash:
        frappe.delete_doc("Docgettr Trash Item", trash, ignore_permissions=True)
    frappe.delete_doc("Docgettr Document", name, ignore_permissions=True)
    append_audit(user.name, "DocumentPermanentlyDeleted", "Docgettr Document", name)
    return {"status": "ok"}


@frappe.whitelist()
def get_versions(name):
    """Return version history for a document."""
    _resolve_doc_for_user(name)
    versions = frappe.get_all(
        "Docgettr Document Version",
        filters={"document": name},
        fields=["name", "version_no", "display_filename", "mime_type",
                "file_size_bytes", "file_attachment", "replaced_at", "replaced_by"],
        order_by="version_no desc",
    )
    return {"versions": versions}


@frappe.whitelist()
def get_blob(name):
    """Return the file URL so the frontend can fetch the binary directly."""
    _, doc = _resolve_doc_for_user(name)
    append_audit(
        frappe.db.get_value("Docgettr User", {"user": frappe.session.user}, "name"),
        "DocumentViewed", "Docgettr Document", name,
    )
    return {
        "file_url": doc.file_attachment,
        "mime_type": doc.mime_type,
        "display_filename": doc.display_filename,
    }
