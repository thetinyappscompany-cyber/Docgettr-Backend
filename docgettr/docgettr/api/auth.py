"""Authentication endpoints — wrap Frappe's auth + Docgettr User profile."""

import os

import frappe

# "Sign in with Google" requests openid/email/profile, but Google echoes the
# granted scopes back normalized and reordered (e.g. the bare "openid email
# profile" aliases, in a different order). oauthlib's fetch_token() treats that
# as a scope mismatch and raises "Scope has changed from ...", which aborts the
# token exchange and makes Google sign-in fail. Relaxing the check tolerates the
# normalization. drive.py sets the same flag, but that only takes effect if the
# drive module happens to be imported first in a given worker — set it here too
# so Google sign-in works regardless of import order.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from docgettr.docgettr.utils.permissions import (
    get_current_docgettr_user,
    require_current_docgettr_user,
    append_audit,
)
from docgettr.docgettr.utils import settings as _settings
from docgettr.docgettr.api import otp as _otp


# Scopes requested for "Sign in with Google" — just enough to identify the user.
GOOGLE_LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _provision_account(email, display_name, password=None, phone=None,
                       mode="Individual", language_pref="en"):
    """Create the Frappe User + Docgettr User profile + Free subscription.

    Shared by email/password signup and Google sign-up. Returns the Docgettr
    User document. Does not log the user in or commit.
    """
    # 1. Create Frappe User
    frappe_user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": display_name,
        "send_welcome_email": 0,
        "user_type": "Website User",
        "roles": [{"role": "Docgettr User"}],
    })
    frappe_user.insert(ignore_permissions=True)
    # Google accounts have no local password; give them a strong random one so
    # the Frappe User is still valid (they always sign in via Google).
    frappe_user.new_password = password or frappe.generate_hash(length=24)
    frappe_user.save(ignore_permissions=True)

    # 2. Create Docgettr User profile
    dg_user = frappe.get_doc({
        "doctype": "Docgettr User",
        "user": email,
        "display_name": display_name,
        "phone": phone,
        "mode": mode,
        "language_pref": language_pref,
        "current_tier": "Free",
        "storage_backend": "CloudVault",
    }).insert(ignore_permissions=True)

    # 3. Create default Free subscription
    frappe.get_doc({
        "doctype": "Docgettr Subscription",
        "user": dg_user.name,
        "tier": "Free",
        "billing_cycle": "None",
        "started_at": frappe.utils.now_datetime(),
        "status": "Active",
        "last_reset_month": frappe.utils.now_datetime().strftime("%m-%Y"),
    }).insert(ignore_permissions=True)

    return dg_user


def _assert_phone_available(phone, exclude_user=None):
    """Ensure a normalized phone isn't already linked to another account."""
    if not phone:
        return
    existing = frappe.db.get_value("Docgettr User", {"phone": phone}, "user")
    if existing and existing != exclude_user:
        frappe.throw("This mobile number is already linked to another account.",
                     frappe.DuplicateEntryError)


@frappe.whitelist(allow_guest=True)
def signup(email, password, display_name, verification_token, phone=None,
           mode="Individual", language_pref="en"):
    """Create a new Docgettr user with a linked Frappe User and a Free subscription.

    The email must have been verified via the OTP flow first — `verification_token`
    is the single-use token returned by `otp.verify_otp` for the `Signup` purpose.
    """
    email = _otp.normalize_email(email)
    phone = _otp.normalize_phone(phone) or None

    if frappe.db.exists("User", email):
        frappe.throw("An account with this email already exists.", frappe.DuplicateEntryError)

    # Proves the user controls this email address.
    _otp.consume_verification(email, "Signup", verification_token)
    _assert_phone_available(phone)

    dg_user = _provision_account(
        email=email,
        display_name=display_name,
        password=password,
        phone=phone,
        mode=mode,
        language_pref=language_pref,
    )

    # Log the user in
    frappe.local.login_manager.user = email
    frappe.local.login_manager.post_login()

    frappe.db.commit()
    return {"user": dg_user.as_dict()}


def _resolve_login_email(identifier):
    """Map a login identifier (email or mobile number) to a Frappe User email."""
    identifier = (identifier or "").strip()
    if not identifier:
        frappe.throw("Enter your email or mobile number.", frappe.AuthenticationError)
    if _otp.looks_like_email(identifier):
        return _otp.normalize_email(identifier)
    # Treat as a phone number → look up the linked account.
    phone = _otp.normalize_phone(identifier)
    email = frappe.db.get_value("Docgettr User", {"phone": phone}, "user")
    if not email:
        # Same generic error as a bad password — don't reveal which phones exist.
        frappe.throw("Invalid login credentials.", frappe.AuthenticationError)
    return email


@frappe.whitelist(allow_guest=True)
def login(password, identifier=None, email=None):
    """Authenticate with an email *or* mobile number + password.

    `identifier` accepts either; `email` is kept for backward compatibility.
    """
    email = _resolve_login_email(identifier or email)

    from frappe.auth import LoginManager
    lm = LoginManager()
    lm.authenticate(user=email, pwd=password)
    lm.post_login()

    dg_name = frappe.db.get_value("Docgettr User", {"user": email}, "name")
    if not dg_name:
        frappe.throw("No Docgettr profile linked to this user.")
    dg_user = frappe.get_doc("Docgettr User", dg_name)
    return {"user": dg_user.as_dict()}


@frappe.whitelist()
def logout():
    frappe.local.login_manager.logout()
    return {"status": "ok"}


@frappe.whitelist(allow_guest=True)
def reset_password(destination, verification_token, new_password):
    """Set a new password after verifying an OTP (`Reset` purpose).

    `destination` is the email or mobile number the user requested the reset
    for; `verification_token` comes from `otp.verify_otp`.
    """
    _otp.consume_verification(destination, "Reset", verification_token)
    email = _resolve_login_email(destination)

    from frappe.utils.password import update_password as _update_password
    _update_password(email, new_password)

    append_audit(
        frappe.db.get_value("Docgettr User", {"user": email}, "name"),
        "PasswordReset", "User", email,
    )
    frappe.db.commit()
    return {"status": "ok"}


@frappe.whitelist()
def set_password(new_password):
    """Set / change the password for the logged-in user.

    Primarily for accounts created via Google sign-in, which start with a
    random password the user never knows.
    """
    user = require_current_docgettr_user()
    from frappe.utils.password import update_password as _update_password
    _update_password(user.user, new_password)
    append_audit(user.name, "PasswordSet", "User", user.user)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Sign in / Sign up with Google
# ---------------------------------------------------------------------------
# Authorization-code flow driven by the Next.js frontend:
#   1. The browser hits the frontend's /api/auth/google route, which asks us
#      for a consent URL (google_login_url) and redirects the user to Google.
#   2. Google redirects back to the frontend callback (/api/auth/google/callback)
#      with a `code`. The callback server-side-calls google_login(code,
#      redirect_uri); we exchange the code, identify the Google account, create
#      or look up the matching Docgettr User, and establish a Frappe session.
#   3. The frontend captures the issued `sid` cookie (exactly like the email
#      login route) and stores it as the HttpOnly `frappe_sid` cookie.
#
# The `redirect_uri` is supplied by (and validated against) the frontend so the
# same code path works in every environment; it must be registered as an
# authorized redirect URI on the Google OAuth client.


def _google_client_config(redirect_uri):
    client_id = _settings.get("google_client_id")
    client_secret = _settings.get("google_client_secret")
    if not (client_id and client_secret):
        frappe.throw(
            "Google sign-in is not configured. Set google_client_id / "
            "google_client_secret in Docgettr Settings.",
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


def _google_flow(redirect_uri):
    """Build the OAuth Flow for sign-in.

    The consent URL and the token exchange are two *separate* HTTP requests
    (google_login_url then google_login), each with its own Flow instance. With
    PKCE on (the library default, autogenerate_code_verifier=True), the consent
    step would generate a code_verifier and send its code_challenge to Google,
    but the token-exchange Flow — a fresh instance — has no verifier to send, so
    Google rejects fetch_token with "invalid_grant: Missing code verifier".
    This is a confidential web client (it authenticates with client_secret), so
    PKCE is redundant: disable verifier auto-generation to keep both steps
    consistent.
    """
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(
        _google_client_config(redirect_uri),
        scopes=GOOGLE_LOGIN_SCOPES,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    return flow


@frappe.whitelist(allow_guest=True)
def google_login_url(redirect_uri, state):
    """Return the Google consent URL for "Sign in with Google".

    `redirect_uri` is the frontend OAuth callback; `state` is an opaque,
    frontend-generated CSRF token that Google echoes back unchanged.
    """
    flow = _google_flow(redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type="online",
        include_granted_scopes="false",
        prompt="select_account",
        state=state,
    )
    return {"auth_url": auth_url}


@frappe.whitelist(allow_guest=True)
def google_login(code, redirect_uri):
    """Exchange a Google auth code, then log in (creating the account if new)."""
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    flow = _google_flow(redirect_uri)
    flow.fetch_token(code=code)
    creds = flow.credentials

    if not creds.id_token:
        frappe.throw("Google did not return an identity token.", frappe.AuthenticationError)

    # Verifies the JWT signature, expiry and audience (our client_id).
    info = google_id_token.verify_oauth2_token(
        creds.id_token, google_requests.Request(), _settings.get("google_client_id"),
    )

    if not info.get("email_verified"):
        frappe.throw("Your Google email is not verified.", frappe.AuthenticationError)

    email = (info.get("email") or "").strip().lower()
    if not email:
        frappe.throw("Google did not return an email address.", frappe.AuthenticationError)
    display_name = info.get("name") or info.get("given_name") or email.split("@")[0]

    created = False
    if frappe.db.exists("User", email):
        # Existing Frappe User — make sure a Docgettr profile is linked.
        dg_name = frappe.db.get_value("Docgettr User", {"user": email}, "name")
        if dg_name:
            dg_user = frappe.get_doc("Docgettr User", dg_name)
        else:
            dg_user = _provision_account(email=email, display_name=display_name)
            created = True
    else:
        dg_user = _provision_account(email=email, display_name=display_name)
        created = True

    # Establish the Frappe session (issues the `sid` cookie the frontend reads).
    frappe.local.login_manager.user = email
    frappe.local.login_manager.post_login()

    append_audit(dg_user.name, "GoogleSignup" if created else "GoogleLogin",
                 "Docgettr User", dg_user.name)
    frappe.db.commit()
    # Return the session id in the body too: the frontend callback is a redirect,
    # and relying solely on the Set-Cookie header surviving a server-to-server
    # fetch + redirect is fragile. The explicit sid is the reliable source.
    return {"user": dg_user.as_dict(), "created": created, "sid": frappe.session.sid}


@frappe.whitelist()
def get_current_user():
    """Return current Docgettr User + Subscription."""
    user = get_current_docgettr_user()
    if not user:
        return {"user": None}
    sub_name = frappe.db.get_value("Docgettr Subscription", {"user": user.name}, "name")
    sub = frappe.get_doc("Docgettr Subscription", sub_name).as_dict() if sub_name else None
    user_dict = user.as_dict()
    # Surface Drive connection state without ever exposing the tokens themselves.
    user_dict["drive_connected"] = bool(
        user.get_password("drive_access_token", raise_exception=False)
    )
    # A profile is "complete" once we have a mobile number on file. Google
    # sign-ups land here without one and must finish setup at /welcome.
    user_dict["profile_complete"] = bool(user.phone)
    return {"user": user_dict, "subscription": sub}


@frappe.whitelist()
def complete_profile(phone, password=None, display_name=None, mode=None):
    """Finish setup for an account that signed up via Google.

    Adds the mobile number (a required login identity) and, optionally, lets
    the user set a real password instead of the random one Google accounts get.
    """
    user = require_current_docgettr_user()
    phone = _otp.normalize_phone(phone) or None
    if not phone:
        frappe.throw("A mobile number is required.")
    _assert_phone_available(phone, exclude_user=user.user)

    user.phone = phone
    if display_name:
        user.display_name = display_name
    if mode:
        user.mode = mode
    user.save(ignore_permissions=True)

    if password:
        from frappe.utils.password import update_password as _update_password
        _update_password(user.user, password)

    append_audit(user.name, "ProfileCompleted", "Docgettr User", user.name)
    frappe.db.commit()
    return {"user": user.as_dict()}


@frappe.whitelist()
def update_profile(display_name=None, phone=None, mode=None, language_pref=None,
                   avatar_seed=None, default_family=None, storage_backend=None):
    """Update the current Docgettr User's profile fields."""
    user = require_current_docgettr_user()
    if display_name is not None: user.display_name = display_name
    if phone is not None:
        phone = _otp.normalize_phone(phone) or None
        _assert_phone_available(phone, exclude_user=user.user)
        user.phone = phone
    if mode is not None: user.mode = mode
    if language_pref is not None: user.language_pref = language_pref
    if avatar_seed is not None: user.avatar_seed = avatar_seed
    if default_family is not None: user.default_family = default_family or None
    if storage_backend is not None: user.storage_backend = storage_backend
    user.save(ignore_permissions=True)
    return {"user": user.as_dict()}


@frappe.whitelist()
def delete_account():
    """Immediate account deletion — for admin/testing. Most users should
    go through export.request_deletion (7-day window)."""
    user = require_current_docgettr_user()
    from docgettr.tasks import wipe_account
    wipe_account(user.name)
    append_audit(user.name, "AccountWiped", "Docgettr User", user.name)
    return {"status": "wiped"}
