import secrets
import frappe
from frappe.model.document import Document

from docgettr.docgettr.utils.id_generator import generate_id
from docgettr.docgettr.utils.tier_caps import get_tier_caps


class DocgettrShareLink(Document):
    def before_insert(self):
        if not self.name:
            self.name = generate_id("shr")
        if not self.token:
            self.token = secrets.token_urlsafe(32)

    def validate(self):
        if not self.expires_at:
            frappe.throw("Share link must have an expiry")
        if frappe.utils.get_datetime(self.expires_at) <= frappe.utils.now_datetime():
            frappe.throw("Expiry must be in the future")

        # Enforce tier-based max expiry
        creator = frappe.get_doc("Docgettr User", self.created_by)
        caps = get_tier_caps(creator.current_tier)
        max_hours = caps.get("max_share_expiry_hours", 24)
        max_expiry = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=max_hours)
        if frappe.utils.get_datetime(self.expires_at) > frappe.utils.get_datetime(max_expiry):
            frappe.throw(
                f"Your plan allows share links of up to {max_hours} hours. "
                "Upgrade for longer-lived links.",
                title="Limit Reached",
            )
