"""DPDP — data export + account deletion."""

import io
import json
import zipfile

import frappe
from frappe.utils.file_manager import save_file

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    append_audit,
)
from docgettr.docgettr.utils import settings as _settings


@frappe.whitelist()
def export_all():
    """DPDP Article 6 — generate a ZIP of all user data."""
    user = require_current_docgettr_user()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        metadata = {
            "user": user.as_dict(),
            "documents": [],
            "families": [],
            "family_memberships": [],
            "share_links": [],
            "reminders": [],
            "consents": [],
            "subscription": None,
            "audit_log": [],
        }

        # Documents + files
        docs = frappe.get_all(
            "Docgettr Document",
            filters={
                "uploaded_by": user.name,
                "status": ["!=", "PermanentlyDeleted"],
            },
            fields=["*"],
        )
        for d in docs:
            metadata["documents"].append(d)
            if d.get("file_attachment"):
                try:
                    file_name = frappe.db.get_value(
                        "File", {"file_url": d["file_attachment"]}, "name"
                    )
                    if file_name:
                        file_doc = frappe.get_doc("File", file_name)
                        content = file_doc.get_content()
                        if isinstance(content, str):
                            content = content.encode()
                        zf.writestr(
                            f"documents/{d['name']}_{d.get('display_filename') or 'file'}",
                            content,
                        )
                except Exception:
                    pass

        # Families (keeper)
        metadata["families"] = frappe.get_all(
            "Docgettr Family",
            filters={"keeper_user": user.name},
            fields=["*"],
        )
        # Family memberships
        metadata["family_memberships"] = frappe.get_all(
            "Docgettr Family Member",
            filters={"user": user.name},
            fields=["*"],
        )
        # Share links
        metadata["share_links"] = frappe.get_all(
            "Docgettr Share Link",
            filters={"created_by": user.name},
            fields=["*"],
        )
        # Reminders
        metadata["reminders"] = frappe.get_all(
            "Docgettr Reminder",
            filters={"owner_user": user.name},
            fields=["*"],
        )
        # Consents
        metadata["consents"] = frappe.get_all(
            "Docgettr Consent Record",
            filters={"user": user.name},
            fields=["*"],
        )
        # Subscription
        sub_name = frappe.db.get_value("Docgettr Subscription", {"user": user.name}, "name")
        if sub_name:
            metadata["subscription"] = frappe.get_doc(
                "Docgettr Subscription", sub_name
            ).as_dict()
        # Audit log
        metadata["audit_log"] = frappe.get_all(
            "Docgettr Audit Log",
            filters={"actor": user.name},
            fields=["*"],
            order_by="ts desc",
            limit_page_length=0,
        )

        zf.writestr(
            "metadata.json",
            json.dumps(metadata, default=str, indent=2, ensure_ascii=False),
        )

    buffer.seek(0)
    fname = f"docgettr_export_{user.name}_{frappe.utils.today()}.zip"
    file_doc = save_file(
        fname=fname,
        content=buffer.read(),
        dt="Docgettr User",
        dn=user.name,
        is_private=1,
    )
    append_audit(user.name, "AccountExported", "Docgettr User", user.name)
    return {"download_url": file_doc.file_url, "filename": fname}


@frappe.whitelist()
def request_deletion():
    user = require_current_docgettr_user()
    grace_days = _settings.get_int("account_deletion_grace_days", 7)
    user.deletion_requested_at = frappe.utils.now_datetime()
    user.auto_wipe_at = frappe.utils.add_days(frappe.utils.now_datetime(), grace_days)
    user.save(ignore_permissions=True)
    append_audit(user.name, "AccountDeletionRequested", "Docgettr User", user.name)

    try:
        frappe.sendmail(
            recipients=[user.user],
            subject="Docgettr — Account deletion scheduled",
            template="account_deletion_warning",
            args={
                "display_name": user.display_name,
                "wipe_date": user.auto_wipe_at,
            },
            now=False,
        )
    except Exception:
        frappe.log_error(message=frappe.get_traceback(), title="Deletion warning email failed")

    return {"auto_wipe_at": user.auto_wipe_at}


@frappe.whitelist()
def cancel_deletion():
    user = require_current_docgettr_user()
    user.deletion_requested_at = None
    user.auto_wipe_at = None
    user.save(ignore_permissions=True)
    append_audit(user.name, "AccountDeletionCancelled", "Docgettr User", user.name)
    return {"status": "cancelled"}


@frappe.whitelist()
def wipe_now(user_name=None):
    """Admin-only: immediately wipe an account (bypasses 7-day window)."""
    actor = require_current_docgettr_user()
    target = user_name or actor.name
    if target != actor.name and not actor.is_admin and \
            "System Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Only admins can wipe other accounts", frappe.PermissionError)
    from docgettr.tasks import wipe_account
    wipe_account(target)
    return {"status": "wiped", "user": target}
