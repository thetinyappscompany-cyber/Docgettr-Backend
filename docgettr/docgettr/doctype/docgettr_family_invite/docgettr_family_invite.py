import frappe
from frappe.model.document import Document

from docgettr.docgettr.utils.id_generator import generate_id


class DocgettrFamilyInvite(Document):
    def before_insert(self):
        if not self.name:
            self.name = generate_id("finv")
