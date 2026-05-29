"""Authentication endpoints — wrap Frappe's auth + Docgettr User profile."""

import frappe

from docgettr.docgettr.utils.permissions import (
    get_current_docgettr_user,
    require_current_docgettr_user,
    append_audit,
)


@frappe.whitelist(allow_guest=True)
def signup(email, password, display_name, phone=None, mode="Individual",
           language_pref="en"):
    """Create a new Docgettr user with a linked Frappe User and a Free subscription."""
    if frappe.db.exists("User", email):
        frappe.throw("An account with this email already exists.", frappe.DuplicateEntryError)

    # 1. Create Frappe User
    frappe_user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": display_name,
        "send_welcome_email": 0,
        "user_type": "Website User",
        "roles": [{"role": "Docgettr User"}],
    })
    frappe_user.insert(ignore_permissions=True)
    frappe_user.new_password = password
    frappe_user.save(ignore_permissions=True)

    # 2. Create Docgettr User profile
    dg_user = frappe.get_doc({
        "doctype": "Docgettr User",
        "user": email,
        "display_name": display_name,
        "phone": phone,
        "mode": mode,
        "language_pref": language_pref,
        "current_tier": "Free",
        "storage_backend": "CloudVault",
    }).insert(ignore_permissions=True)

    # 3. Create default Free subscription
    frappe.get_doc({
        "doctype": "Docgettr Subscription",
        "user": dg_user.name,
        "tier": "Free",
        "billing_cycle": "None",
        "started_at": frappe.utils.now_datetime(),
        "status": "Active",
        "last_reset_month": frappe.utils.now_datetime().strftime("%m-%Y"),
    }).insert(ignore_permissions=True)

    # 4. Log the user in
    frappe.local.login_manager.user = email
    frappe.local.login_manager.post_login()

    frappe.db.commit()
    return {"user": dg_user.as_dict()}


@frappe.whitelist(allow_guest=True)
def login(email, password):
    """Authenticate and return the Docgettr User profile."""
    from frappe.auth import LoginManager
    lm = LoginManager()
    lm.authenticate(user=email, pwd=password)
    lm.post_login()

    dg_name = frappe.db.get_value("Docgettr User", {"user": email}, "name")
    if not dg_name:
        frappe.throw("No Docgettr profile linked to this user.")
    dg_user = frappe.get_doc("Docgettr User", dg_name)
    return {"user": dg_user.as_dict()}


@frappe.whitelist()
def logout():
    frappe.local.login_manager.logout()
    return {"status": "ok"}


@frappe.whitelist()
def get_current_user():
    """Return current Docgettr User + Subscription."""
    user = get_current_docgettr_user()
    if not user:
        return {"user": None}
    sub_name = frappe.db.get_value("Docgettr Subscription", {"user": user.name}, "name")
    sub = frappe.get_doc("Docgettr Subscription", sub_name).as_dict() if sub_name else None
    user_dict = user.as_dict()
    # Surface Drive connection state without ever exposing the tokens themselves.
    user_dict["drive_connected"] = bool(
        user.get_password("drive_access_token", raise_exception=False)
    )
    return {"user": user_dict, "subscription": sub}


@frappe.whitelist()
def update_profile(display_name=None, phone=None, mode=None, language_pref=None,
                   avatar_seed=None, default_family=None, storage_backend=None):
    """Update the current Docgettr User's profile fields."""
    user = require_current_docgettr_user()
    if display_name is not None: user.display_name = display_name
    if phone is not None: user.phone = phone
    if mode is not None: user.mode = mode
    if language_pref is not None: user.language_pref = language_pref
    if avatar_seed is not None: user.avatar_seed = avatar_seed
    if default_family is not None: user.default_family = default_family or None
    if storage_backend is not None: user.storage_backend = storage_backend
    user.save(ignore_permissions=True)
    return {"user": user.as_dict()}


@frappe.whitelist()
def delete_account():
    """Immediate account deletion — for admin/testing. Most users should
    go through export.request_deletion (7-day window)."""
    user = require_current_docgettr_user()
    from docgettr.tasks import wipe_account
    wipe_account(user.name)
    append_audit(user.name, "AccountWiped", "Docgettr User", user.name)
    return {"status": "wiped"}
