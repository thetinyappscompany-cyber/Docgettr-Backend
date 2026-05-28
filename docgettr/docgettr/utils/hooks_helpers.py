"""Doc-event handlers registered in hooks.py:doc_events."""

import frappe

from docgettr.docgettr.utils.permissions import append_audit


def on_document_created(doc, method=None):
    # Hook already covered by controller's after_insert — kept as a no-op
    # for forward compatibility (e.g., webhooks, Drive auto-sync).
    pass


def on_document_deleted(doc, method=None):
    if doc.uploaded_by:
        append_audit(
            doc.uploaded_by,
            "DocumentPermanentlyDeleted",
            "Docgettr Document",
            doc.name,
        )
