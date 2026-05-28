import frappe
from frappe.model.document import Document


class DocgettrSettings(Document):
    def on_update(self):
        # Bust the in-process cache so reads in the same request see updates
        frappe.cache().delete_value("docgettr_settings_cache")
