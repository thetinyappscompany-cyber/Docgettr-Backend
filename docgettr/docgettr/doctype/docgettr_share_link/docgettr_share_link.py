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

        # Normalize the incoming value to Frappe's naive datetime string before any
        # comparison. Clients may send an ISO-8601 value with a trailing "Z"
        # (e.g. JS `toISOString()`), which parses to a tz-aware datetime and would
        # otherwise crash the comparisons below against the tz-naive now_datetime().
        expires_at = frappe.utils.get_datetime(self.expires_at)
        if expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)
        self.expires_at = frappe.utils.format_datetime(expires_at, "yyyy-MM-dd HH:mm:ss")

        now = frappe.utils.now_datetime()
        if expires_at <= now:
            frappe.throw("Expiry must be in the future")

        # Enforce tier-based max expiry, allowing a small grace margin so harmless
        # client/server clock skew on an at-the-cap link isn't rejected.
        creator = frappe.get_doc("Docgettr User", self.created_by)
        caps = get_tier_caps(creator.current_tier)
        max_hours = caps.get("max_share_expiry_hours", 24)
        max_expiry = frappe.utils.add_to_date(now, hours=max_hours, minutes=5)
        if expires_at > frappe.utils.get_datetime(max_expiry):
            frappe.throw(
                f"Your plan allows share links of up to {max_hours} hours. "
                "Upgrade for longer-lived links.",
                title="Limit Reached",
            )
