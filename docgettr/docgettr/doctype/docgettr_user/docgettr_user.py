import frappe
from frappe.model.document import Document

from docgettr.docgettr.utils.id_generator import generate_id


class DocgettrUser(Document):
    def before_insert(self):
        if not self.name:
            self.name = generate_id("usr")

    def validate(self):
        # Sync display name to Frappe User
        if self.user and self.has_value_changed("display_name"):
            try:
                fu = frappe.get_doc("User", self.user)
                fu.first_name = self.display_name
                fu.save(ignore_permissions=True)
            except Exception:
                pass
