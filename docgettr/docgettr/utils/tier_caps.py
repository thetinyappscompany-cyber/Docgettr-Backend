import frappe


TIER_CAPS = {
    "Free": {
        "max_documents": 50,
        "max_family_members": 3,
        "max_ai_scans": 25,
        "max_storage_bytes": 250 * 1024 * 1024,
        "max_share_expiry_hours": 24,
    },
    "PremiumIndividual": {
        "max_documents": 1000,
        "max_family_members": 5,
        "max_ai_scans": 200,
        "max_storage_bytes": 5 * 1024 * 1024 * 1024,
        "max_share_expiry_hours": 720,
    },
    "PremiumFamily": {
        "max_documents": -1,
        "max_family_members": 10,
        "max_ai_scans": 500,
        "max_storage_bytes": 20 * 1024 * 1024 * 1024,
        "max_share_expiry_hours": 720,
    },
}


def get_tier_caps(tier: str) -> dict:
    return TIER_CAPS.get(tier, TIER_CAPS["Free"])


def get_document_count(user_name: str) -> int:
    return frappe.db.count(
        "Docgettr Document",
        {"uploaded_by": user_name, "status": "Active"},
    )


def get_storage_used_bytes(user_name: str) -> int:
    result = frappe.db.sql(
        """
        SELECT COALESCE(SUM(file_size_bytes), 0)
        FROM `tabDocgettr Document`
        WHERE uploaded_by = %s AND status != 'PermanentlyDeleted'
        """,
        user_name,
    )
    return int(result[0][0]) if result else 0


def enforce_document_cap(user) -> None:
    caps = get_tier_caps(user.current_tier)
    if caps["max_documents"] == -1:
        return
    count = get_document_count(user.name)
    if count >= caps["max_documents"]:
        frappe.throw(
            f"Document limit reached ({caps['max_documents']}). "
            "Upgrade your plan to upload more.",
            title="Limit Reached",
        )


def enforce_storage_cap(user, file_size: int = 0) -> None:
    caps = get_tier_caps(user.current_tier)
    sub = frappe.db.get_value(
        "Docgettr Subscription",
        {"user": user.name},
        ["addon_storage_gb"],
        as_dict=True,
    ) or {"addon_storage_gb": 0}
    addon_bytes = int(sub.get("addon_storage_gb") or 0) * 1024 * 1024 * 1024
    max_bytes = caps["max_storage_bytes"] + addon_bytes

    used = get_storage_used_bytes(user.name)
    if used + (file_size or 0) > max_bytes:
        frappe.throw(
            "Storage limit reached. Upgrade or delete some documents.",
            title="Storage Full",
        )


def reset_if_new_month(subscription) -> None:
    current_month = frappe.utils.now_datetime().strftime("%m-%Y")
    if subscription.last_reset_month != current_month:
        subscription.ai_scans_used_this_month = 0
        subscription.last_reset_month = current_month
        subscription.save(ignore_permissions=True)
