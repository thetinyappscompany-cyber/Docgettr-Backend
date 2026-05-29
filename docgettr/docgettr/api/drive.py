"""Google Drive integration — OAuth + sync.

OAuth tokens are stored encrypted on Docgettr User (Password fields).
"""

import io
import json

import frappe

from docgettr.docgettr.utils.permissions import (
    require_current_docgettr_user,
    append_audit,
)
from docgettr.docgettr.utils import settings as _settings


GOOGLE_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

# How long a signed OAuth `state` value stays valid (seconds).
_STATE_MAX_AGE = 600


def _root_folder_name() -> str:
    return _settings.get("drive_root_folder_name") or "Docgettr"


# ---------------------------------------------------------------------------
# OAuth state signing + post-callback redirect helpers
# ---------------------------------------------------------------------------
# The OAuth callback returns from Google as a top-level browser navigation to
# the Frappe domain with NO Docgettr session cookie. We therefore identify the
# user from the `state` param. To stop an attacker forging a `state` for someone
# else's account, `state` is an itsdangerous-signed, time-limited token rather
# than the bare user name.

def _state_serializer():
    from itsdangerous import URLSafeTimedSerializer
    from frappe.utils.password import get_encryption_key

    return URLSafeTimedSerializer(get_encryption_key(), salt="docgettr-drive-oauth")


def _make_state(user_name: str) -> str:
    return _state_serializer().dumps(user_name)


def _user_from_state(state: str):
    """Resolve and validate the Docgettr User encoded in a signed `state`."""
    from itsdangerous import BadSignature, SignatureExpired

    if not state:
        frappe.throw("Missing OAuth state", frappe.AuthenticationError)
    try:
        user_name = _state_serializer().loads(state, max_age=_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        frappe.throw("Invalid or expired OAuth state", frappe.AuthenticationError)
    if not user_name or not frappe.db.exists("Docgettr User", user_name):
        frappe.throw("Unknown Docgettr User in OAuth state", frappe.AuthenticationError)
    return frappe.get_doc("Docgettr User", user_name)


def _app_url() -> str:
    return (_settings.get("app_url") or "").rstrip("/")


def _redirect_to_app(path: str) -> None:
    """Issue a 302 to the Next.js frontend (a top-level browser navigation)."""
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"{_app_url()}{path}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_config():
    client_id = _settings.get("google_client_id")
    client_secret = _settings.get("google_client_secret")
    redirect_uri = _settings.get("google_redirect_uri")
    if not (client_id and client_secret and redirect_uri):
        frappe.throw(
            "Google Drive is not configured. Set google_client_id / _secret / _redirect_uri "
            "in Docgettr Settings.",
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _get_drive_service(user):
    """Build a Google Drive client for the given Docgettr User."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    access = user.get_password("drive_access_token", raise_exception=False)
    refresh = user.get_password("drive_refresh_token", raise_exception=False)
    if not access:
        frappe.throw("Google Drive is not connected for this user.")

    client_id = _settings.get("google_client_id")
    client_secret = _settings.get("google_client_secret")

    creds = Credentials(
        token=access,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_DRIVE_SCOPES,
    )
    if not creds.valid and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        # Persist refreshed access token
        user.drive_access_token = creds.token
        user.save(ignore_permissions=True)

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _ensure_folder(service, name: str, parent_id: str = None) -> str:
    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"
    found = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
    files = found.get("files") or []
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return service.files().create(body=meta, fields="id").execute()["id"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_auth_url():
    from google_auth_oauthlib.flow import Flow
    user = require_current_docgettr_user()
    flow = Flow.from_client_config(_client_config(), scopes=GOOGLE_DRIVE_SCOPES)
    flow.redirect_uri = _settings.get("google_redirect_uri")
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=_make_state(user.name),
    )
    return {"auth_url": auth_url, "state": state}


@frappe.whitelist(allow_guest=True)
def handle_callback(code=None, state=None, error=None, **kwargs):
    """Google OAuth redirect target.

    This is a top-level browser navigation back from Google to the Frappe
    domain, so there is NO Docgettr session here — the user is identified from
    the signed `state` param, not from the session. On completion we 302 the
    browser back to the frontend rather than returning JSON.
    """
    from google_auth_oauthlib.flow import Flow

    # User denied consent (or Google returned an error).
    if error or not code:
        return _redirect_to_app("/settings/storage?drive=error")

    try:
        user = _user_from_state(state)

        flow = Flow.from_client_config(_client_config(), scopes=GOOGLE_DRIVE_SCOPES)
        flow.redirect_uri = _settings.get("google_redirect_uri")
        flow.fetch_token(code=code)
        creds = flow.credentials

        user.drive_access_token = creds.token
        if creds.refresh_token:
            user.drive_refresh_token = creds.refresh_token
        if creds.expiry:
            user.drive_token_expiry = creds.expiry
        user.save(ignore_permissions=True)

        # Bootstrap the Docgettr root folder so subsequent syncs are fast
        try:
            service = _get_drive_service(user)
            root_id = _ensure_folder(service, _root_folder_name())
            user.drive_root_folder_id = root_id
            user.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(message=frappe.get_traceback(), title="Drive bootstrap failed")

        append_audit(user.name, "DriveConnected", "Docgettr User", user.name)
    except Exception:
        frappe.log_error(message=frappe.get_traceback(), title="Drive OAuth callback failed")
        return _redirect_to_app("/settings/storage?drive=error")

    return _redirect_to_app("/settings/storage?drive=connected")


@frappe.whitelist()
def disconnect():
    user = require_current_docgettr_user()
    user.drive_access_token = None
    user.drive_refresh_token = None
    user.drive_token_expiry = None
    user.drive_root_folder_id = None
    user.save(ignore_permissions=True)
    append_audit(user.name, "DriveDisconnected", "Docgettr User", user.name)
    return {"status": "ok"}


@frappe.whitelist()
def sync_document(document_name):
    """Upload a Docgettr Document's file to the user's Drive."""
    from googleapiclient.http import MediaIoBaseUpload

    user = require_current_docgettr_user()
    doc = frappe.get_doc("Docgettr Document", document_name)
    if doc.uploaded_by != user.name and not user.is_admin:
        frappe.throw("Not authorized", frappe.PermissionError)

    service = _get_drive_service(user)
    root_id = user.drive_root_folder_id or _ensure_folder(service, _root_folder_name())

    # Folder structure: /Docgettr/{MemberName or "Personal"}/{Category}/
    member_name = "Personal"
    if doc.belongs_to_member:
        member_name = frappe.db.get_value(
            "Docgettr Family Member", doc.belongs_to_member, "display_name"
        ) or "Personal"
    category_label = "Other"
    if doc.category:
        category_label = frappe.db.get_value(
            "Docgettr Category", doc.category, "display_label"
        ) or "Other"

    member_folder = _ensure_folder(service, member_name, root_id)
    category_folder = _ensure_folder(service, category_label, member_folder)

    # Pull file bytes from Frappe File
    file_name = frappe.db.get_value("File", {"file_url": doc.file_attachment}, "name")
    if not file_name:
        frappe.throw("Source file missing")
    file_doc = frappe.get_doc("File", file_name)
    content = file_doc.get_content()
    if isinstance(content, str):
        content = content.encode()
    media = MediaIoBaseUpload(io.BytesIO(content),
                              mimetype=doc.mime_type or "application/octet-stream")
    meta = {"name": doc.display_filename, "parents": [category_folder]}
    result = service.files().create(body=meta, media_body=media, fields="id").execute()
    drive_file_id = result["id"]

    doc.drive_file_id = drive_file_id
    doc.save(ignore_permissions=True)
    append_audit(user.name, "DriveSynced", "Docgettr Document", document_name,
                 context={"drive_file_id": drive_file_id})
    return {"drive_file_id": drive_file_id}


@frappe.whitelist()
def import_from_drive(file_ids):
    """Create Docgettr Document rows pointing at existing Drive files.
    file_ids: JSON array of Drive file IDs."""
    user = require_current_docgettr_user()
    if isinstance(file_ids, str):
        file_ids = json.loads(file_ids)
    service = _get_drive_service(user)

    created = []
    for fid in file_ids or []:
        try:
            meta = service.files().get(
                fileId=fid, fields="id,name,mimeType,size",
            ).execute()
            doc = frappe.get_doc({
                "doctype": "Docgettr Document",
                "uploaded_by": user.name,
                "display_filename": meta.get("name"),
                "original_filename": meta.get("name"),
                "file_attachment": "",  # Empty since the bytes live in Drive
                "mime_type": meta.get("mimeType"),
                "file_size_bytes": int(meta.get("size") or 0),
                "storage_backend": "MyDrive",
                "drive_file_id": meta["id"],
                "status": "Active",
                "version_no": 1,
            }).insert(ignore_permissions=True)
            created.append(doc.name)
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title=f"Drive import failed: {fid}",
            )
    return {"created": created}


@frappe.whitelist()
def set_storage_backend(backend):
    if backend not in ("CloudVault", "MyDrive"):
        frappe.throw("backend must be CloudVault or MyDrive")
    user = require_current_docgettr_user()
    user.storage_backend = backend
    user.save(ignore_permissions=True)
    return {"storage_backend": backend}
