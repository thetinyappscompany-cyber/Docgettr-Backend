import frappe


def format_indian_date(value):
    """Render a date as DD-MMM-YYYY for emails. Safe on None."""
    if not value:
        return ""
    try:
        dt = frappe.utils.get_datetime(value)
    except Exception:
        return str(value)
    return dt.strftime("%d-%b-%Y")
