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


def _invite_dict(row):
    """Enrich a raw invite row/doc with the family + user names the UI needs."""
    d = row.as_dict() if hasattr(row, "as_dict") else dict(row)
    invited = frappe.db.get_value(
        "Docgettr User", d.get("invited_user"),
        ["display_name", "avatar_seed", "user"], as_dict=True,
    ) or {}
    d["family_name"] = frappe.db.get_value("Docgettr Family", d.get("family"), "family_name")
    d["inviter_display_name"] = frappe.db.get_value("Docgettr User", d.get("inviter"), "display_name")
    d["invited_display_name"] = invited.get("display_name")
    d["invited_avatar_seed"] = invited.get("avatar_seed")
    d["invited_email"] = invited.get("user")
    return d


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
def find_user_by_email(email):
    """Resolve a single Docgettr user by their *exact* email, for family invites.

    Deliberately an exact-match lookup (never a list or prefix search) so the
    user base cannot be enumerated: the caller must already know the address of
    the person they want to add. Requires an authenticated Docgettr user and
    returns only the minimal fields needed to render and link a member.
    """
    require_current_docgettr_user()
    email = (email or "").strip().lower()
    if not email:
        return {"user": None}
    row = frappe.db.get_value(
        "Docgettr User",
        {"user": email},
        ["name", "display_name", "avatar_seed", "user"],
        as_dict=True,
    )
    if not row:
        return {"user": None}
    return {"user": {
        "name": row.name,
        "display_name": row.display_name,
        "avatar_seed": row.avatar_seed,
        "email": row.user,
    }}


@frappe.whitelist()
def add_member(family, kind, role, display_name, user=None,
               relationship=None, avatar_seed=None, dob=None, notes=None):
    actor = require_current_docgettr_user()
    if not _is_family_admin(actor.name, family) and \
            actor.name != frappe.db.get_value("Docgettr Family", family, "keeper_user"):
        frappe.throw("Only family admins can add members", frappe.PermissionError)

    # A Linked member maps to a real account; don't let the same account be
    # added to one family twice.
    if kind == "Linked" and user and frappe.db.exists(
        "Docgettr Family Member", {"family": family, "user": user}
    ):
        frappe.throw("That person is already a member of this family.")

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


# ───────── Linked-member invitations (consent flow) ─────────
#
# A Linked member is a real account, so they aren't dropped into a family
# silently: the admin sends an invitation, and the person only becomes a member
# once they accept it themselves.

@frappe.whitelist()
def invite_member(family, invited_email, role="Editor", display_name=None):
    actor = require_current_docgettr_user()
    keeper = frappe.db.get_value("Docgettr Family", family, "keeper_user")
    if not _is_family_admin(actor.name, family) and actor.name != keeper:
        frappe.throw("Only family admins can invite members", frappe.PermissionError)

    invited_email = (invited_email or "").strip().lower()
    invited = frappe.db.get_value(
        "Docgettr User", {"user": invited_email},
        ["name", "display_name"], as_dict=True,
    )
    if not invited:
        frappe.throw("No Docgettr account uses that email.")
    if invited.name == actor.name:
        frappe.throw("You can't invite yourself.")
    if invited.name == keeper or frappe.db.exists(
        "Docgettr Family Member", {"family": family, "user": invited.name}
    ):
        frappe.throw("That person is already a member of this family.")
    if frappe.db.exists(
        "Docgettr Family Invite",
        {"family": family, "invited_user": invited.name, "status": "Pending"},
    ):
        frappe.throw("An invitation is already pending for that person.")

    invite = frappe.get_doc({
        "doctype": "Docgettr Family Invite",
        "family": family,
        "inviter": actor.name,
        "invited_user": invited.name,
        "role": role,
        "display_name": display_name or (invited.display_name or "").split(" ")[0],
        "status": "Pending",
    }).insert(ignore_permissions=True)

    append_audit(actor.name, "FamilyInviteSent", "Docgettr Family Invite", invite.name,
                 context={"family": family, "invited_user": invited.name, "role": role})
    return {"invite": _invite_dict(invite)}


@frappe.whitelist()
def list_my_invites():
    """Pending invitations addressed to the current user."""
    actor = require_current_docgettr_user()
    rows = frappe.get_all(
        "Docgettr Family Invite",
        filters={"invited_user": actor.name, "status": "Pending"},
        fields=["*"],
        order_by="creation desc",
    )
    return {"invites": [_invite_dict(r) for r in rows]}


@frappe.whitelist()
def list_family_invites(family):
    """Pending invitations sent for a family (visible to its members)."""
    actor = require_current_docgettr_user()
    if not _user_can_access_family(actor.name, family):
        frappe.throw("Not authorized", frappe.PermissionError)
    rows = frappe.get_all(
        "Docgettr Family Invite",
        filters={"family": family, "status": "Pending"},
        fields=["*"],
        order_by="creation desc",
    )
    return {"invites": [_invite_dict(r) for r in rows]}


@frappe.whitelist()
def accept_invite(invite_id):
    actor = require_current_docgettr_user()
    invite = frappe.get_doc("Docgettr Family Invite", invite_id)
    if invite.invited_user != actor.name:
        frappe.throw("This invitation isn't addressed to you.", frappe.PermissionError)
    if invite.status != "Pending":
        frappe.throw("This invitation is no longer pending.")

    member = None
    if not frappe.db.exists(
        "Docgettr Family Member", {"family": invite.family, "user": actor.name}
    ):
        member = frappe.get_doc({
            "doctype": "Docgettr Family Member",
            "family": invite.family,
            "user": actor.name,
            "kind": "Linked",
            "role": invite.role,
            "display_name": invite.display_name or (actor.display_name or "").split(" ")[0],
            "relationship": "Other",
            "avatar_seed": actor.avatar_seed,
        }).insert(ignore_permissions=True)
        append_audit(actor.name, "FamilyMemberAdded", "Docgettr Family Member", member.name,
                     context={"family": invite.family, "kind": "Linked", "via_invite": invite.name})

    invite.status = "Accepted"
    invite.responded_at = frappe.utils.now_datetime()
    invite.save(ignore_permissions=True)
    append_audit(actor.name, "FamilyInviteAccepted", "Docgettr Family Invite", invite.name,
                 context={"family": invite.family})
    return {"member": member.as_dict() if member else None, "invite": _invite_dict(invite)}


@frappe.whitelist()
def decline_invite(invite_id):
    actor = require_current_docgettr_user()
    invite = frappe.get_doc("Docgettr Family Invite", invite_id)
    if invite.invited_user != actor.name:
        frappe.throw("This invitation isn't addressed to you.", frappe.PermissionError)
    if invite.status != "Pending":
        frappe.throw("This invitation is no longer pending.")
    invite.status = "Declined"
    invite.responded_at = frappe.utils.now_datetime()
    invite.save(ignore_permissions=True)
    append_audit(actor.name, "FamilyInviteDeclined", "Docgettr Family Invite", invite.name,
                 context={"family": invite.family})
    return {"status": "ok"}


@frappe.whitelist()
def revoke_invite(invite_id):
    actor = require_current_docgettr_user()
    invite = frappe.get_doc("Docgettr Family Invite", invite_id)
    keeper = frappe.db.get_value("Docgettr Family", invite.family, "keeper_user")
    if actor.name != invite.inviter and not _is_family_admin(actor.name, invite.family) \
            and actor.name != keeper:
        frappe.throw("Only the inviter or a family admin can revoke this.", frappe.PermissionError)
    if invite.status != "Pending":
        frappe.throw("This invitation is no longer pending.")
    invite.status = "Revoked"
    invite.responded_at = frappe.utils.now_datetime()
    invite.save(ignore_permissions=True)
    append_audit(actor.name, "FamilyInviteRevoked", "Docgettr Family Invite", invite.name,
                 context={"family": invite.family, "invited_user": invite.invited_user})
    return {"status": "ok"}
