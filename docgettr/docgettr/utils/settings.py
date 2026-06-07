"""
Centralised settings access with a fallback chain:

    Docgettr Settings singleton  →  frappe.conf (site_config.json)  →  hardcoded default

Hot fields (API keys, tier caps, pricing, lifecycle knobs) are managed via
the Docgettr Settings Singleton in the Desk. site_config.json keys are
still honoured as a fallback so existing deployments don't break and so
keys can be set via `bench set-config` in scripts.

Reads are cheap — Frappe caches Singletons in process memory.
"""

import frappe


# ---------------------------------------------------------------------------
# Hardcoded defaults — used only if both Settings and site_config are empty.
# Keep these in sync with the DocType field defaults.
# ---------------------------------------------------------------------------

DEFAULTS = {
    # AI / Gemini. Keep these on current GA models — older 1.5 models were
    # retired by Google in 2025 and now 404 at the API.
    "gemini_model_primary": "gemini-2.5-flash",
    "gemini_model_fallback": "gemini-2.0-flash",
    "ai_confidence_threshold": 0.65,
    "drive_root_folder_name": "Docgettr",

    # Frontend (Next.js) base URL — used for post-OAuth browser redirects.
    "app_url": "https://docgettr.com",

    # Pricing (paise)
    "price_individual_monthly_paise": 4900,
    "price_individual_annual_paise": 29900,
    "price_family_monthly_paise": 9900,
    "price_family_annual_paise": 59900,

    # Tier — Free
    "free_max_documents": 50,
    "free_max_family_members": 3,
    "free_max_ai_scans": 25,
    "free_max_storage_mb": 250,
    "free_max_share_expiry_hours": 24,

    # Tier — Premium Individual
    "premium_individual_max_documents": 1000,
    "premium_individual_max_family_members": 5,
    "premium_individual_max_ai_scans": 200,
    "premium_individual_max_storage_mb": 5120,
    "premium_individual_max_share_expiry_hours": 720,

    # Tier — Premium Family
    "premium_family_max_documents": -1,
    "premium_family_max_family_members": 10,
    "premium_family_max_ai_scans": 500,
    "premium_family_max_storage_mb": 20480,
    "premium_family_max_share_expiry_hours": 720,

    # Lifecycle
    "trash_purge_days": 30,
    "account_deletion_grace_days": 7,
    "storage_warning_threshold_pct": 85,
}


# Fields that should be read via Document.get_password to decrypt
PASSWORD_FIELDS = {
    "gemini_api_key",
    "razorpay_key_secret",
    "razorpay_webhook_secret",
    "google_client_secret",
}


def get_settings_doc():
    """Return the Singleton, falling back to a fresh in-memory doc if the
    row doesn't exist yet (e.g. during the first install before seed)."""
    try:
        return frappe.get_cached_doc("Docgettr Settings", "Docgettr Settings")
    except frappe.DoesNotExistError:
        return None


def get(key: str, default=None):
    """Read a setting: Singleton field → site_config → DEFAULTS → caller default."""
    doc = get_settings_doc()
    if doc is not None:
        # Encrypted fields need decryption
        if key in PASSWORD_FIELDS:
            try:
                value = doc.get_password(key, raise_exception=False)
            except Exception:
                value = None
        else:
            value = doc.get(key)
        if value not in (None, ""):
            return value

    # Fall back to site_config.json (bench set-config keys)
    conf_value = frappe.conf.get(key)
    if conf_value not in (None, ""):
        return conf_value

    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


def get_int(key: str, default: int = 0) -> int:
    value = get(key, default)
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    value = get(key, default)
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Tier caps — built from individual fields above
# ---------------------------------------------------------------------------

def get_tier_caps(tier: str) -> dict:
    """Return the live tier-cap dict for the given tier."""
    prefix_by_tier = {
        "Free": "free",
        "PremiumIndividual": "premium_individual",
        "PremiumFamily": "premium_family",
    }
    prefix = prefix_by_tier.get(tier, "free")
    return {
        "max_documents": get_int(f"{prefix}_max_documents"),
        "max_family_members": get_int(f"{prefix}_max_family_members"),
        "max_ai_scans": get_int(f"{prefix}_max_ai_scans"),
        "max_storage_bytes": get_int(f"{prefix}_max_storage_mb") * 1024 * 1024,
        "max_share_expiry_hours": get_int(f"{prefix}_max_share_expiry_hours"),
    }


def get_price_paise(tier: str, cycle: str) -> int:
    """Razorpay pricing in paise."""
    table = {
        ("PremiumIndividual", "Monthly"): "price_individual_monthly_paise",
        ("PremiumIndividual", "Annual"): "price_individual_annual_paise",
        ("PremiumFamily", "Monthly"): "price_family_monthly_paise",
        ("PremiumFamily", "Annual"): "price_family_annual_paise",
    }
    field = table.get((tier, cycle))
    return get_int(field) if field else 0


# ---------------------------------------------------------------------------
# Defaults seeding
# ---------------------------------------------------------------------------

def ensure_defaults():
    """Make sure the Singleton has a row with sensible defaults."""
    try:
        doc = frappe.get_doc("Docgettr Settings", "Docgettr Settings")
        dirty = False
        for key, value in DEFAULTS.items():
            if doc.get(key) in (None, "", 0) and key.startswith((
                "free_", "premium_", "price_", "trash_", "account_", "storage_",
                "ai_", "gemini_", "drive_",
            )):
                # Only fill if blank, and only for numeric/string default fields
                # (skip if value is 0 and field is legitimately 0)
                doc.set(key, value)
                dirty = True
        if dirty:
            doc.save(ignore_permissions=True)
    except Exception:
        # Singleton row doesn't exist yet — create one with defaults
        try:
            doc = frappe.new_doc("Docgettr Settings")
            for key, value in DEFAULTS.items():
                doc.set(key, value)
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title="Docgettr Settings: seed defaults failed",
            )
