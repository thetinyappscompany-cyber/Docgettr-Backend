import frappe
from frappe.model.document import Document


class DocgettrOTP(Document):
    """A one-time passcode challenge.

    Codes are never stored in the clear — only a salted hash. A successful
    verification flips the row to ``Verified`` and issues a single-use
    ``verification_token`` that the signup / reset endpoints exchange (and
    then ``Consumed``) so the OTP step and the account action stay decoupled.
    """

    def is_expired(self) -> bool:
        return bool(self.expires_at) and frappe.utils.now_datetime() > frappe.utils.get_datetime(
            self.expires_at
        )
