"""Scheduled jobs wired in hooks.py:scheduler_events.

Each function is idempotent — safe to retry.
"""

import frappe

from docgettr.docgettr.utils.permissions import append_audit
from docgettr.docgettr.utils import settings as _settings


# ---------------------------------------------------------------------------
# Hourly
# ---------------------------------------------------------------------------

def fire_pending_reminders():
    """Fire reminders whose `fire_at` is in the past."""
    now = frappe.utils.now_datetime()
    reminders = frappe.get_all(
        "Docgettr Reminder",
        filters={
            "status": ["in", ["Pending", "Snoozed"]],
            "fire_at": ["<=", now],
        },
        fields=["name", "owner_user", "title", "body", "channel_email",
                "channel_push", "document"],
    )
    for r in reminders:
        try:
            user_email = frappe.db.get_value(
                "Docgettr User", r["owner_user"], "user"
            )
            if r.get("channel_email") and user_email:
                frappe.sendmail(
                    recipients=[user_email],
                    subject=f"Docgettr Reminder: {r['title']}",
                    template="reminder_email",
                    args={
                        "title": r["title"],
                        "body": r.get("body") or "",
                        "document": r.get("document"),
                    },
                    now=False,
                )
            frappe.db.set_value("Docgettr Reminder", r["name"], "status", "Fired")
            append_audit(r["owner_user"], "ReminderFired",
                         "Docgettr Reminder", r["name"])
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"Reminder fire failed: {r['name']}",
            )
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Daily
# ---------------------------------------------------------------------------

def expire_share_links():
    """Mark expired share links as Expired."""
    frappe.db.sql(
        """
        UPDATE `tabDocgettr Share Link`
        SET status = 'Expired'
        WHERE status = 'Active' AND expires_at <= %s
        """,
        frappe.utils.now_datetime(),
    )
    frappe.db.commit()


def purge_expired_trash():
    """Permanently delete documents in trash past 30 days."""
    now = frappe.utils.now_datetime()
    expired = frappe.get_all(
        "Docgettr Trash Item",
        filters={"auto_purge_at": ["<=", now]},
        fields=["name", "original_doc", "deleted_by"],
    )
    for item in expired:
        try:
            doc_name = item["original_doc"]
            doc = frappe.get_doc("Docgettr Document", doc_name)
            # Drop the file
            if doc.file_attachment:
                file_name = frappe.db.get_value(
                    "File", {"file_url": doc.file_attachment}, "name"
                )
                if file_name:
                    try:
                        frappe.delete_doc("File", file_name, ignore_permissions=True)
                    except Exception:
                        pass
            doc.status = "PermanentlyDeleted"
            doc.save(ignore_permissions=True)
            frappe.delete_doc("Docgettr Trash Item", item["name"], ignore_permissions=True)
            append_audit(
                item["deleted_by"], "DocumentPermanentlyDeleted",
                "Docgettr Document", doc_name,
            )
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"Trash purge failed: {item['name']}",
            )
    frappe.db.commit()


def process_account_deletions():
    """Wipe accounts whose 7-day grace period has passed."""
    now = frappe.utils.now_datetime()
    users = frappe.get_all(
        "Docgettr User",
        filters=[
            ["auto_wipe_at", "<=", now],
            ["auto_wipe_at", "is", "set"],
        ],
        pluck="name",
    )
    for name in users:
        try:
            wipe_account(name)
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"Account wipe failed: {name}",
            )
    frappe.db.commit()


def reset_monthly_scan_counts():
    """Reset ai_scans_used_this_month for all subscriptions if month has changed."""
    current_month = frappe.utils.now_datetime().strftime("%m-%Y")
    frappe.db.sql(
        """
        UPDATE `tabDocgettr Subscription`
        SET ai_scans_used_this_month = 0, last_reset_month = %s
        WHERE last_reset_month != %s OR last_reset_month IS NULL
        """,
        (current_month, current_month),
    )
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Weekly
# ---------------------------------------------------------------------------

def send_storage_warnings():
    """Email users approaching their storage cap (>=85%)."""
    from docgettr.docgettr.utils.tier_caps import (
        get_tier_caps, get_storage_used_bytes,
    )
    users = frappe.get_all(
        "Docgettr User",
        fields=["name", "user", "current_tier", "display_name"],
    )
    for u in users:
        try:
            caps = get_tier_caps(u["current_tier"])
            used = get_storage_used_bytes(u["name"])
            max_bytes = caps["max_storage_bytes"]
            if max_bytes <= 0:
                continue
            ratio = used / max_bytes
            threshold = _settings.get_int("storage_warning_threshold_pct", 85) / 100.0
            if ratio >= threshold and u.get("user"):
                pct = round(ratio * 100, 1)
                frappe.sendmail(
                    recipients=[u["user"]],
                    subject=f"Docgettr: You're at {pct}% storage",
                    message=(
                        f"Hi {u['display_name']},\n\n"
                        f"You've used {pct}% of your storage quota on Docgettr. "
                        "Upgrade your plan or clear out unused documents to make room."
                    ),
                    now=False,
                )
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"Storage warning failed: {u['name']}",
            )


# ---------------------------------------------------------------------------
# DPDP — full account wipe
# ---------------------------------------------------------------------------

def wipe_account(docgettr_user_name: str):
    """Delete everything related to this Docgettr user. Idempotent."""
    user_row = frappe.db.get_value(
        "Docgettr User", docgettr_user_name, ["user"], as_dict=True
    )
    if not user_row:
        return  # Already gone

    # Documents + files
    doc_names = frappe.get_all(
        "Docgettr Document",
        filters={"uploaded_by": docgettr_user_name},
        pluck="name",
    )
    for dn in doc_names:
        try:
            doc = frappe.get_doc("Docgettr Document", dn)
            if doc.file_attachment:
                fn = frappe.db.get_value(
                    "File", {"file_url": doc.file_attachment}, "name"
                )
                if fn:
                    try:
                        frappe.delete_doc("File", fn, ignore_permissions=True)
                    except Exception:
                        pass
            frappe.delete_doc("Docgettr Document", dn, ignore_permissions=True)
        except Exception:
            pass

    # Doc-related rows
    for dt, key in [
        ("Docgettr Document Version", "replaced_by"),
        ("Docgettr Share Link", "created_by"),
        ("Docgettr Reminder", "owner_user"),
        ("Docgettr Subscription", "user"),
        ("Docgettr Consent Record", "user"),
        ("Docgettr Trash Item", "deleted_by"),
        ("Docgettr Smart Folder", "user"),
        ("Docgettr Access Request", "requester"),
        ("Docgettr Audit Log", "actor"),
    ]:
        try:
            frappe.db.delete(dt, {key: docgettr_user_name})
        except Exception:
            pass

    # Family memberships
    frappe.db.delete("Docgettr Family Member", {"user": docgettr_user_name})

    # Families where this user is the keeper
    families = frappe.get_all(
        "Docgettr Family",
        filters={"keeper_user": docgettr_user_name},
        pluck="name",
    )
    for fam in families:
        frappe.db.delete("Docgettr Family Member", {"family": fam})
        try:
            frappe.delete_doc("Docgettr Family", fam, ignore_permissions=True)
        except Exception:
            pass

    # Docgettr User itself
    try:
        frappe.delete_doc("Docgettr User", docgettr_user_name, ignore_permissions=True)
    except Exception:
        pass

    # Disable the Frappe User (keep the row for audit trail)
    if user_row.get("user"):
        try:
            frappe.db.set_value("User", user_row["user"], "enabled", 0)
        except Exception:
            pass

    frappe.db.commit()


# ---------------------------------------------------------------------------
# Daily — OTP housekeeping
# ---------------------------------------------------------------------------

def purge_stale_otps():
    """Delete OTP rows older than a day (expired, consumed, or abandoned)."""
    cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-1)
    frappe.db.delete("Docgettr OTP", {"creation": ("<", cutoff)})
    frappe.db.commit()
