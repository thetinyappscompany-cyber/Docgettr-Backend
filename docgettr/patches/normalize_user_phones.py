"""Blank-out empty phone strings so the new unique index on
`Docgettr User.phone` can be applied (MySQL allows many NULLs but not many
empty strings). Run before the schema sync adds the unique constraint.
"""

import frappe


def execute():
    if not frappe.db.has_column("Docgettr User", "phone"):
        return
    frappe.db.sql(
        """UPDATE `tabDocgettr User` SET phone = NULL WHERE phone = '' OR phone IS NULL"""
    )
    frappe.db.commit()
