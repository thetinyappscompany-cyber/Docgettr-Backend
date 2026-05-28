"""Gemini-backed classification + extraction. Server-side keeps the API key
out of the browser."""

import json
import re

import frappe
from frappe.utils.file_manager import get_file

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    can_edit_document,
    append_audit,
)
from docgettr.docgettr.utils.tier_caps import get_tier_caps, reset_if_new_month
from docgettr.docgettr.utils.ai_prompts import (
    build_master_classify_prompt,
    build_extraction_prompt,
    build_reclassify_prompt,
)
from docgettr.docgettr.utils.validators import (
    parse_indian_date,
    render_filename_template,
)


CLASSIFY_CONFIDENCE_THRESHOLD = 0.65


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_consent(user_name: str):
    has_consent = frappe.db.exists("Docgettr Consent Record", {
        "user": user_name, "purpose": "AiProcessing", "status": "Granted",
    })
    if not has_consent:
        frappe.throw(
            "AI Processing consent is required. Please grant consent in Settings.",
            frappe.PermissionError,
        )


def _enforce_and_increment_scan(user):
    sub_name = frappe.db.get_value("Docgettr Subscription", {"user": user.name}, "name")
    if not sub_name:
        frappe.throw("No subscription record found", frappe.DoesNotExistError)
    sub = frappe.get_doc("Docgettr Subscription", sub_name)
    reset_if_new_month(sub)
    caps = get_tier_caps(user.current_tier)
    if sub.ai_scans_used_this_month >= caps["max_ai_scans"]:
        frappe.throw(
            f"AI scan limit reached ({caps['max_ai_scans']}/month). Upgrade your plan.",
            title="Limit Reached",
        )
    sub.ai_scans_used_this_month = (sub.ai_scans_used_this_month or 0) + 1
    sub.save(ignore_permissions=True)


def _load_catalogue() -> list:
    return frappe.get_all(
        "Docgettr Document Type",
        fields=["name", "slug", "display_name_en", "category", "ai_prompt_hints_json"],
        order_by="catalog_number asc",
    )


def _get_file_bytes(file_url: str):
    """Return (bytes, content_type) for a Frappe File URL."""
    file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not file_name:
        frappe.throw(f"File not found: {file_url}")
    file_doc = frappe.get_doc("File", file_name)
    return file_doc.get_content(), file_doc.content_type


def _gemini_model(model_name: str = None):
    import google.generativeai as genai
    api_key = frappe.conf.get("gemini_api_key")
    if not api_key:
        frappe.throw("Server is missing gemini_api_key in site_config.")
    genai.configure(api_key=api_key)
    model = (
        model_name
        or frappe.conf.get("gemini_model_primary")
        or "gemini-2.0-flash"
    )
    return genai.GenerativeModel(model)


def _parse_json_response(text: str) -> dict:
    """Strip fences and parse a Gemini JSON-mode response defensively."""
    if not text:
        return {}
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (ValueError, TypeError):
                pass
    return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def classify(document_name):
    """Two-phase classify+extract for an uploaded document.

    Side effects: updates the document with type/category/fields,
    increments AI scan counter, audits the call.
    """
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document_name)
    if not can_edit_document(user.name, doc):
        frappe.throw("Not authorized to run AI on this document", frappe.PermissionError)

    _check_consent(user.name)
    _enforce_and_increment_scan(user)

    file_bytes, mime = _get_file_bytes(doc.file_attachment)
    catalogue = _load_catalogue()

    # ---- Phase 1: classification ----
    model = _gemini_model()
    classify_prompt = build_master_classify_prompt(catalogue)
    try:
        response = model.generate_content(
            [classify_prompt, {"mime_type": mime, "data": file_bytes}],
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        )
    except Exception as exc:
        frappe.log_error(message=frappe.get_traceback(), title="Gemini classify failed")
        frappe.throw(f"AI classification failed: {exc}")

    result = _parse_json_response(getattr(response, "text", ""))
    doc.document_type = result.get("document_type_id") or None
    if doc.document_type:
        doc.category = frappe.db.get_value(
            "Docgettr Document Type", doc.document_type, "category"
        ) or doc.category
    doc.ai_confidence_overall = float(result.get("overall_confidence") or 0)
    doc.ocr_text = result.get("ocr_text") or doc.ocr_text

    extraction = {}
    per_field_conf = {}

    # ---- Phase 2: extraction (only if confident) ----
    if doc.ai_confidence_overall >= CLASSIFY_CONFIDENCE_THRESHOLD and doc.document_type:
        doc_type = frappe.get_doc("Docgettr Document Type", doc.document_type)
        extract_prompt = build_extraction_prompt(doc_type)
        try:
            extract_response = model.generate_content(
                [extract_prompt, {"mime_type": mime, "data": file_bytes}],
                generation_config={
                    "temperature": 0.1,
                    "response_mime_type": "application/json",
                },
            )
            extract_result = _parse_json_response(getattr(extract_response, "text", ""))
            extraction = extract_result.get("fields") or {}
            per_field_conf = extract_result.get("per_field_confidence") or {}
        except Exception:
            frappe.log_error(message=frappe.get_traceback(), title="Gemini extract failed")

        doc.ai_extracted_fields_json = json.dumps(extraction)
        doc.ai_confidence_per_field_json = json.dumps(per_field_conf)

        # Auto-set expiry date if the doc type defines one
        if doc_type.has_expiry and doc_type.expiry_field_key:
            raw_expiry = extraction.get(doc_type.expiry_field_key)
            iso = parse_indian_date(raw_expiry) if raw_expiry else None
            if iso:
                doc.expiry_date = iso

        # Render display_filename from template
        if doc_type.file_naming_template:
            rendered = render_filename_template(doc_type.file_naming_template, extraction)
            if rendered:
                doc.display_filename = rendered

    doc.save(ignore_permissions=True)
    append_audit(user.name, "AiScanInvoked", "Docgettr Document", document_name,
                 context={"confidence": doc.ai_confidence_overall,
                          "document_type": doc.document_type})

    return {
        "document_type": doc.document_type,
        "category": doc.category,
        "confidence": doc.ai_confidence_overall,
        "fields": extraction,
        "per_field_confidence": per_field_conf,
        "ocr_text": doc.ocr_text,
        "reasoning": result.get("reasoning", ""),
        "display_filename": doc.display_filename,
        "expiry_date": doc.expiry_date,
    }


@frappe.whitelist()
def extract_fields(document_name):
    """Run extraction phase only — assumes document_type is already set."""
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document_name)
    if not can_edit_document(user.name, doc):
        frappe.throw("Not authorized", frappe.PermissionError)
    if not doc.document_type:
        frappe.throw("Document has no document_type set; call classify first.")

    _check_consent(user.name)
    _enforce_and_increment_scan(user)

    file_bytes, mime = _get_file_bytes(doc.file_attachment)
    doc_type = frappe.get_doc("Docgettr Document Type", doc.document_type)
    model = _gemini_model()
    prompt = build_extraction_prompt(doc_type)
    response = model.generate_content(
        [prompt, {"mime_type": mime, "data": file_bytes}],
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
    )
    result = _parse_json_response(getattr(response, "text", ""))
    fields = result.get("fields") or {}
    per_field = result.get("per_field_confidence") or {}

    doc.ai_extracted_fields_json = json.dumps(fields)
    doc.ai_confidence_per_field_json = json.dumps(per_field)
    if doc_type.has_expiry and doc_type.expiry_field_key:
        iso = parse_indian_date(fields.get(doc_type.expiry_field_key))
        if iso:
            doc.expiry_date = iso
    doc.save(ignore_permissions=True)
    append_audit(user.name, "AiScanInvoked", "Docgettr Document", document_name)
    return {"fields": fields, "per_field_confidence": per_field,
            "warnings": result.get("warnings", [])}


@frappe.whitelist()
def reclassify(document_name, rejected_type):
    """Re-classify a document after the user rejected the initial guess."""
    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document_name)
    if not can_edit_document(user.name, doc):
        frappe.throw("Not authorized", frappe.PermissionError)

    _check_consent(user.name)
    _enforce_and_increment_scan(user)

    file_bytes, mime = _get_file_bytes(doc.file_attachment)
    catalogue = _load_catalogue()
    prompt = build_reclassify_prompt(catalogue, rejected_type)
    model = _gemini_model()
    response = model.generate_content(
        [prompt, {"mime_type": mime, "data": file_bytes}],
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
    )
    result = _parse_json_response(getattr(response, "text", ""))
    doc.document_type = result.get("document_type_id") or None
    doc.ai_confidence_overall = float(result.get("overall_confidence") or 0)
    if doc.document_type:
        doc.category = frappe.db.get_value(
            "Docgettr Document Type", doc.document_type, "category"
        ) or doc.category
    doc.save(ignore_permissions=True)
    append_audit(user.name, "AiScanInvoked", "Docgettr Document", document_name,
                 context={"reclassified_from": rejected_type})
    return {
        "document_type": doc.document_type,
        "confidence": doc.ai_confidence_overall,
        "reasoning": result.get("reasoning", ""),
    }
