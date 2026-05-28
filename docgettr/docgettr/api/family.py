"""Family + family member management."""

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    append_audit,
)


def _is_family_admin(user_name: str, family_name: str) -> bool:
    role = frappe.db.get_value(
        "Docgettr Family Member",
        {"family": family_name, "user": user_name},
        "role",
    )
    return role == "Admin"


def _user_can_access_family(user_name: str, family_name: str) -> bool:
    keeper = frappe.db.get_value("Docgettr Family", family_name, "keeper_user")
    if keeper == user_name:
        return True
    return bool(frappe.db.exists(
        "Docgettr Family Member",
        {"family": family_name, "user": user_name},
    ))


@frappe.whitelist()
def create(family_name, cover_emoji=None):
    user = require_current_docgettr_user()
    family = frappe.get_doc({
        "doctype": "Docgettr Family",
        "family_name": family_name,
        "keeper_user": user.name,
        "cover_emoji": cover_emoji,
    }).insert(ignore_permissions=True)
    if not user.default_family:
        user.default_family = family.name
        user.save(ignore_permissions=True)
    return {"family": family.as_dict()}


@frappe.whitelist()
def get(name):
    user = require_current_docgettr_user()
    if not _user_can_access_family(user.name, name):
        frappe.throw("Not authorized", frappe.PermissionError)
    family = frappe.get_doc("Docgettr Family", name)
    return {"family": family.as_dict()}


@frappe.whitelist()
def list_families():
    user = require_current_docgettr_user()
    keeper_families = frappe.get_all(
        "Docgettr Family",
        filters={"keeper_user": user.name},
        fields=["*"],
    )
    member_family_names = frappe.get_all(
        "Docgettr Family Member",
        filters={"user": user.name},
        pluck="family",
    )
    member_families = (
        frappe.get_all(
            "Docgettr Family",
            filters={"name": ["in", member_family_names]},
            fields=["*"],
        )
        if member_family_names
        else []
    )
    seen = set()
    result = []
    for f in keeper_families + member_families:
        if f.name not in seen:
            seen.add(f.name)
            result.append(f)
    return {"families": result}


@frappe.whitelist()
def list_members(family):
    user = require_current_docgettr_user()
    if not _user_can_access_family(user.name, family):
        frappe.throw("Not authorized", frappe.PermissionError)
    members = frappe.get_all(
        "Docgettr Family Member",
        filters={"family": family},
        fields=["*"],
        order_by="creation asc",
    )
    return {"members": members}


@frappe.whitelist()
def get_member(member_id):
    member = frappe.get_doc("Docgettr Family Member", member_id)
    user = require_current_docgettr_user()
    if not _user_can_access_family(user.name, member.family):
        frappe.throw("Not authorized", frappe.PermissionError)
    return {"member": member.as_dict()}


@frappe.whitelist()
def add_member(family, kind, role, display_name, user=None,
               relationship=None, avatar_seed=None, dob=None, notes=None):
    actor = require_current_docgettr_user()
    if not _is_family_admin(actor.name, family) and \
            actor.name != frappe.db.get_value("Docgettr Family", family, "keeper_user"):
        frappe.throw("Only family admins can add members", frappe.PermissionError)

    member = frappe.get_doc({
        "doctype": "Docgettr Family Member",
        "family": family,
        "user": user or None,
        "kind": kind,
        "role": role,
        "display_name": display_name,
        "relationship": relationship,
        "avatar_seed": avatar_seed,
        "dob": dob,
        "notes": notes,
    }).insert(ignore_permissions=True)

    append_audit(actor.name, "FamilyMemberAdded", "Docgettr Family Member", member.name,
                 context={"family": family, "kind": kind, "role": role})
    return {"member": member.as_dict()}


@frappe.whitelist()
def update_member(member_id, patch):
    import json
    actor = require_current_docgettr_user()
    member = frappe.get_doc("Docgettr Family Member", member_id)

    if not _is_family_admin(actor.name, member.family) and \
            actor.name != frappe.db.get_value("Docgettr Family", member.family, "keeper_user"):
        frappe.throw("Only family admins can update members", frappe.PermissionError)

    if isinstance(patch, str):
        patch = json.loads(patch or "{}")
    editable = {"role", "display_name", "relationship", "avatar_seed", "dob", "notes", "kind"}
    for k, v in (patch or {}).items():
        if k in editable:
            setattr(member, k, v)
    member.save(ignore_permissions=True)
    return {"member": member.as_dict()}


@frappe.whitelist()
def remove_member(member_id):
    actor = require_current_docgettr_user()
    member = frappe.get_doc("Docgettr Family Member", member_id)
    family = frappe.get_doc("Docgettr Family", member.family)
    if actor.name != family.keeper_user and not _is_family_admin(actor.name, member.family):
        frappe.throw("Only family admins can remove members", frappe.PermissionError)
    if member.user == family.keeper_user:
        frappe.throw("Cannot remove the keeper from their own family")
    append_audit(actor.name, "FamilyMemberRemoved", "Docgettr Family Member", member_id,
                 context={"family": member.family})
    frappe.delete_doc("Docgettr Family Member", member_id, ignore_permissions=True)
    return {"status": "ok"}
