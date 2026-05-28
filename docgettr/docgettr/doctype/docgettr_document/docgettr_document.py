import frappe
from frappe.model.document import Document

from docgettr.docgettr.utils.id_generator import generate_id
from docgettr.docgettr.utils.tier_caps import (
    enforce_document_cap,
    enforce_storage_cap,
)
from docgettr.docgettr.utils.permissions import append_audit
from docgettr.docgettr.utils import settings as _settings


class DocgettrDocument(Document):
    def before_insert(self):
        if not self.name:
            self.name = generate_id("doc")

        # Default uploaded_by to current Docgettr user if not set
        if not self.uploaded_by:
            dg = frappe.db.get_value("Docgettr User", {"user": frappe.session.user}, "name")
            if dg:
                self.uploaded_by = dg

        # Default category from document_type
        if self.document_type and not self.category:
            self.category = frappe.db.get_value(
                "Docgettr Document Type", self.document_type, "category"
            )

        # Tier cap enforcement (only on Active uploads)
        if self.status == "Active" and self.uploaded_by:
            user = frappe.get_doc("Docgettr User", self.uploaded_by)
            enforce_document_cap(user)
            enforce_storage_cap(user, file_size=self.file_size_bytes or 0)

    def after_insert(self):
        if self.uploaded_by:
            append_audit(self.uploaded_by, "DocumentUploaded", "Docgettr Document", self.name)

    def on_update(self):
        if self.has_value_changed("status"):
            if self.status == "Trashed":
                self._create_trash_item()
            elif self.status == "Active" and self.uploaded_by:
                append_audit(self.uploaded_by, "DocumentRestored", "Docgettr Document", self.name)

    def _create_trash_item(self):
        if frappe.db.exists("Docgettr Trash Item", {"original_doc": self.name}):
            return
        purge_days = _settings.get_int("trash_purge_days", 30)
        frappe.get_doc({
            "doctype": "Docgettr Trash Item",
            "original_doc": self.name,
            "deleted_by": self.uploaded_by,
            "deleted_ts": frappe.utils.now_datetime(),
            "auto_purge_at": frappe.utils.add_days(frappe.utils.now_datetime(), purge_days),
            "reason": "Manual delete",
        }).insert(ignore_permissions=True)
        append_audit(self.uploaded_by, "DocumentSoftDeleted", "Docgettr Document", self.name)
