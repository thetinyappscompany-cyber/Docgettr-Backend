import frappe
from frappe.model.document import Document

from docgettr.docgettr.utils.id_generator import generate_id


class DocgettrFamily(Document):
    def before_insert(self):
        if not self.name:
            self.name = generate_id("fam")

    def after_insert(self):
        # Auto-create a Family Member for the keeper with role=Admin
        keeper = frappe.get_doc("Docgettr User", self.keeper_user)
        frappe.get_doc({
            "doctype": "Docgettr Family Member",
            "family": self.name,
            "user": self.keeper_user,
            "kind": "Linked",
            "role": "Admin",
            "display_name": keeper.display_name,
        }).insert(ignore_permissions=True)
