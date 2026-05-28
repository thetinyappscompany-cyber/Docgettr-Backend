"""Read-only wrappers around the seeded Category / Document Type catalogue."""

import frappe


@frappe.whitelist()
def list_categories():
    cats = frappe.get_all(
        "Docgettr Category",
        fields=["name", "slug", "display_label", "display_order",
                "icon_lucide", "color_hex", "description"],
        order_by="display_order asc",
    )
    return {"categories": cats}


@frappe.whitelist()
def list_document_types(category=None):
    filters = {"category": category} if category else {}
    types = frappe.get_all(
        "Docgettr Document Type",
        filters=filters,
        fields=["*"],
        order_by="catalog_number asc",
    )
    return {"document_types": types}


@frappe.whitelist()
def get_document_type(type_id):
    return {"document_type": frappe.get_doc("Docgettr Document Type", type_id).as_dict()}
