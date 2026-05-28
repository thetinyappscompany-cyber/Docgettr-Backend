import frappe
from frappe.model.document import Document

from docgettr.docgettr.utils.id_generator import generate_id
from docgettr.docgettr.utils.tier_caps import get_tier_caps


class DocgettrFamilyMember(Document):
    def before_insert(self):
        if not self.name:
            self.name = generate_id("mem")
        self._enforce_member_cap()

    def _enforce_member_cap(self):
        family = frappe.get_doc("Docgettr Family", self.family)
        keeper = frappe.get_doc("Docgettr User", family.keeper_user)
        caps = get_tier_caps(keeper.current_tier)
        # Linked members count against the cap; Managed members are unlimited on PremiumFamily
        if self.kind == "Linked":
            current = frappe.db.count("Docgettr Family Member", {
                "family": self.family, "kind": "Linked"
            })
            if current >= caps["max_family_members"]:
                frappe.throw(
                    f"Family member limit reached ({caps['max_family_members']}). "
                    "Upgrade your plan to add more.",
                    title="Limit Reached",
                )
