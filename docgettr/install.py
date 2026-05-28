import frappe

from docgettr.docgettr.utils import settings as _settings


def after_install():
    """Run after `bench install-app docgettr` on a site."""
    create_roles()
    _settings.ensure_defaults()
    frappe.db.commit()


def create_roles():
    for role_name in ("Docgettr User", "Docgettr Admin"):
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({
                "doctype": "Role",
                "role_name": role_name,
                "desk_access": 0 if role_name == "Docgettr User" else 1,
            }).insert(ignore_permissions=True)
