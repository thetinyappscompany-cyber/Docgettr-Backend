"""
Gemini prompt builders. Mirrors the frontend's `src/lib/ai/prompts.ts`
so re-classification behaviour stays consistent if the user wants to
A/B test the same image against the same prompts.
"""

import json


def build_master_classify_prompt(catalogue: list) -> str:
    """
    catalogue: list of {slug, display_name_en, category, ai_prompt_hints_json}
    """
    catalogue_compact = [
        {
            "document_type_id": c.get("slug") or c.get("name"),
            "display_name": c.get("display_name_en"),
            "category": c.get("category"),
            "hints": _safe_load(c.get("ai_prompt_hints_json")),
        }
        for c in catalogue
    ]
    return (
        "You are Docgettr AI — an expert Indian document classifier.\n\n"
        "You will receive an image of a personal document commonly found in Indian households.\n\n"
        "Your task:\n"
        "1. Identify which document type this is from the catalogue below.\n"
        "2. Provide a confidence score (0.0 to 1.0).\n"
        "3. Extract the full OCR text preserving original script (Hindi, Gujarati, English).\n"
        "4. Explain your reasoning briefly.\n\n"
        "CATALOGUE:\n"
        f"{json.dumps(catalogue_compact, ensure_ascii=False)}\n\n"
        "DISAMBIGUATION RULES:\n"
        "- PAN Card: Always has \"INCOME TAX DEPARTMENT\" and a 10-char alphanumeric code (ABCDE1234F pattern).\n"
        "- Aadhaar Card: Always has \"UNIQUE IDENTIFICATION AUTHORITY OF INDIA\" and a 12-digit number.\n"
        "- Voter ID (EPIC): Has \"ELECTION COMMISSION OF INDIA\" and starts with 3 letters + 7 digits.\n"
        "- Driving License: Has \"TRANSPORT DEPARTMENT\" or \"RTO\" and a license number format.\n"
        "- Passport: Has \"REPUBLIC OF INDIA\" and \"PASSPORT\" prominently, MRZ zone at bottom.\n\n"
        "Respond in JSON of shape:\n"
        '{"document_type_id": "<slug>", "category": "<category-slug>", '
        '"overall_confidence": 0.0, "ocr_text": "...", "reasoning": "..."}\n'
        "If the document does not match anything in the catalogue, set "
        "document_type_id to null and overall_confidence to a low value.\n"
    )


def build_extraction_prompt(doc_type) -> str:
    schema = _safe_load(getattr(doc_type, "fields_schema_json", None)) or []
    hints = _safe_load(getattr(doc_type, "ai_prompt_hints_json", None)) or {}
    category_display = getattr(doc_type, "category", "") or ""
    return (
        "You are Docgettr AI — an expert document field extractor.\n\n"
        f"Document type: {doc_type.display_name_en}\n"
        f"Category: {category_display}\n\n"
        "Extract the following fields from this document image:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "RULES:\n"
        "- Dates: Normalize to DD/MM/YYYY format.\n"
        "- Names: Preserve original script + provide English transliteration when possible.\n"
        "- Currency: Format as ₹X,XX,XXX.XX (Indian number grouping).\n"
        "- Addresses: Keep full address as-is, preserve line breaks.\n"
        "- ID numbers: Remove inner spaces (Aadhaar 1234 5678 9012 → 123456789012).\n"
        "- If a field is not visible or unreadable, set it to null.\n"
        f"{('Hints: ' + json.dumps(hints, ensure_ascii=False)) if hints else ''}\n\n"
        "Respond in JSON of shape:\n"
        '{"fields": {"<key>": "<value>", ...}, '
        '"per_field_confidence": {"<key>": 0.0, ...}, '
        '"warnings": ["..."]}\n'
    )


def build_reclassify_prompt(catalogue: list, rejected_type_id: str) -> str:
    base = build_master_classify_prompt(catalogue)
    return (
        f"The user rejected the initial classification of \"{rejected_type_id}\".\n"
        f"Please re-examine the document image and suggest a DIFFERENT document type.\n"
        f"Do NOT suggest \"{rejected_type_id}\" again.\n\n"
        f"{base}"
    )


def _safe_load(value):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None
