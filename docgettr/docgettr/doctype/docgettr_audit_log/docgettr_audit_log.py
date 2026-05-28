import frappe
from frappe.model.document import Document


class DocgettrAuditLog(Document):
    def on_update(self):
        if not getattr(self, "_allow_update", False):
            frappe.throw("Audit log entries are immutable")

    def on_trash(self):
        frappe.throw("Audit log entries cannot be deleted")
