# Docgettr — Frappe Backend

The server-side companion to the Docgettr Next.js PWA. Implements the full
`DataService` contract the frontend expects, plus DPDP compliance, AI
classification, Razorpay payments, and Google Drive sync — all backed by
Frappe v15.

## Repository layout

```
docgettr/
├── hooks.py                  # App registration, scheduler, has_permission, fixtures
├── install.py                # Creates Docgettr User / Docgettr Admin roles
├── tasks.py                  # Hourly/daily/weekly scheduled jobs
├── modules.txt               # Single module: "Docgettr"
├── docgettr/                 # The "Docgettr" module
│   ├── doctype/              # 15 DocTypes
│   ├── api/                  # Whitelisted REST endpoints (17 modules)
│   ├── utils/                # tier_caps, permissions, id_generator, ai_prompts, …
│   ├── fixtures/             # 10 categories + 100 document types
│   └── templates/            # reminder, share, deletion-warning emails
└── requirements.txt
```

## Install on Frappe Cloud

```bash
# From your local bench, or via the Frappe Cloud Bench shell:
bench get-app docgettr <git-url-of-this-repo>
bench --site <your-site> install-app docgettr
bench --site <your-site> migrate
bench --site <your-site> import-fixtures
```

`import-fixtures` seeds the 10 categories and 100 document types.

## Configuration

**Preferred: Docgettr Settings (Desk UI).** After install, open
`Desk → Docgettr → Docgettr Settings` (Singleton) and fill in API keys,
tier caps, pricing, and lifecycle knobs. Defaults are pre-filled.

**Fallback: `site_config.json`.** Every Settings field is also read from
`frappe.conf` if the Settings field is blank, so existing `bench set-config`
deployments keep working. Set via
`bench --site <site> set-config <key> "<value>"`.

| Key                          | Purpose                                            |
|------------------------------|----------------------------------------------------|
| `gemini_api_key`             | Google Gemini API key (server-only)                |
| `gemini_model_primary`       | Default model, e.g. `gemini-2.0-flash`             |
| `gemini_model_fallback`      | Fallback model, e.g. `gemini-1.5-flash`            |
| `razorpay_key_id`            | Razorpay key id (`rzp_live_…` or `rzp_test_…`)     |
| `razorpay_key_secret`        | Razorpay key secret                                |
| `razorpay_webhook_secret`    | (optional) for the webhook endpoint                |
| `google_client_id`           | Google OAuth client ID (Drive)                     |
| `google_client_secret`       | Google OAuth client secret                         |
| `google_redirect_uri`        | `https://<your-site>/api/method/docgettr.docgettr.api.drive.handle_callback` |

The Settings Singleton also exposes:

- **Pricing** — Razorpay amounts in paise for each (tier, cycle)
- **Tier caps** — per-tier limits on documents, storage, AI scans,
  family members, share-link expiry
- **Lifecycle** — trash purge window (default 30 days), DPDP deletion
  grace (default 7 days), storage-warning threshold (default 85%)
- **AI** — classification confidence threshold (default 0.65),
  primary/fallback model names
- **Drive** — root folder name (default `Docgettr`)

You'll also want to relax CORS so the Next.js frontend can reach the API:

```bash
bench --site <site> set-config allow_cors "*"     # or your domain
bench --site <site> set-config ignore_csrf 1      # dev only
```

For production, restrict `allow_cors` to your real frontend origin.

## Whitelisted API endpoints

All endpoints are reachable at:

```
POST  https://<site>/api/method/docgettr.docgettr.api.<module>.<function>
```

### `auth`
- `signup(email, password, display_name, verification_token, phone?, mode?, language_pref?)` — guest; `verification_token` from `otp.verify_otp` (Signup) proves email ownership
- `login(password, identifier?, email?)` — guest; `identifier` accepts an email **or** mobile number
- `google_login_url(redirect_uri, state)` / `google_login(code, redirect_uri)` — guest; Sign in with Google
- `complete_profile(phone, password?, display_name?, mode?)` — finishes Google sign-ups (adds mobile, optional password)
- `reset_password(destination, verification_token, new_password)` — guest; OTP-based (Reset)
- `set_password(new_password)` — change password for the logged-in user
- `logout()`
- `get_current_user()` — also returns `profile_complete` (false until a mobile is on file)
- `update_profile(display_name?, phone?, mode?, language_pref?, avatar_seed?, default_family?, storage_backend?)`
- `delete_account()` — immediate wipe (admin/testing)

### `otp`
- `request_otp(destination, purpose)` — guest; `purpose` ∈ {`Signup`, `Reset`}. Phase 1 delivers codes by **email** (phone destinations resolve to the account's email); SMS is wired but inert until an SMS gateway is configured in Docgettr Settings.
- `verify_otp(destination, code, purpose)` — guest; returns a single-use `verification_token`

### `documents`
- `upload(...)` — multipart `file` field required
- `get(name)`
- `query(filters?, search?, sort_by?, sort_order?, page?, page_size?)`
- `update(name, patch)`
- `replace_file(name)` — multipart `file` field required
- `soft_delete(name)`
- `restore(name)`
- `permanent_delete(name)`
- `get_versions(name)`
- `get_blob(name)` — returns file URL

### `family`
- `create(family_name, cover_emoji?)`
- `get(name)`
- `list_families()`
- `list_members(family)`
- `get_member(member_id)`
- `add_member(family, kind, role, display_name, user?, relationship?, avatar_seed?, dob?, notes?)`
- `update_member(member_id, patch)`
- `remove_member(member_id)`

### `catalogue`
- `list_categories()`
- `list_document_types(category?)`
- `get_document_type(type_id)`

### `ai`
- `classify(document_name)` — runs phase 1 + phase 2 against Gemini
- `extract_fields(document_name)`
- `reclassify(document_name, rejected_type)`

### `sharing`
- `create_link(document, expires_at, password?, max_views?, watermark_enabled?, recipient_label?)`
- `list_links(document)`
- `revoke_link(name)`
- `resolve_link(token, password?)` — **guest**, used by the public share page

### `reminders`
- `list_reminders(status?)`
- `upsert(document, kind, fire_at, title, body?, offset_days?, channel_email?, channel_push?, channel_sms?, name?)`
- `snooze(name, until)`
- `complete(name)`
- `delete_for_document(document)`

### `smart_folders`
- `list_folders()`
- `upsert(label, filter_json, name?, icon_lucide?, display_order?, is_system?)`
- `delete(name)`

### `access_requests`
- `create(document, note?)`
- `list_requests(role)` — `role=owner|requester`
- `resolve(name, decision, note?)` — decision is `Approved` or `Denied`

### `payments`
- `create_order(tier, billing_cycle)`
- `verify_payment(razorpay_order_id, razorpay_payment_id, razorpay_signature)`
- `razorpay_webhook()` — guest, signature-verified

### `subscription`
- `get_subscription()`
- `get_usage()` — replaces the frontend's local quota counters
- `set_tier(user_name, tier)` — admin-only

### `drive`
- `get_auth_url()`
- `handle_callback(code, state)`
- `disconnect()`
- `sync_document(document_name)`
- `import_from_drive(file_ids)`
- `set_storage_backend(backend)`

### `consent`
- `list_consents()`
- `grant(purpose, notice_version?)`
- `revoke(purpose)`

### `export`
- `export_all()` — DPDP ZIP
- `request_deletion()`
- `cancel_deletion()`
- `wipe_now(user_name?)` — admin-only

### `audit`
- `list_audit(action?, target?, page?, page_size?, from_ts?, to_ts?)`

### `trash`
- `list_trash()`

## Tier caps (`utils/tier_caps.py`)

| Capability         | Free   | Premium Individual | Premium Family |
|--------------------|--------|--------------------|----------------|
| Max documents      | 50     | 1,000              | Unlimited      |
| Max family members | 3      | 5                  | 10 Linked      |
| AI scans / month   | 25     | 200                | 500            |
| Storage            | 250 MB | 5 GB               | 20 GB          |
| Share max expiry   | 24 hrs | 30 days            | 30 days        |

The frontend currently has slightly different caps in
`src/lib/data/index.ts` — those should be updated in a later session to
match the server's enforcement.

## Scheduled jobs (`tasks.py`)

| Schedule | Job                          | Purpose                                          |
|----------|------------------------------|--------------------------------------------------|
| Hourly   | `fire_pending_reminders`     | Fire reminders whose `fire_at` is in the past    |
| Daily    | `expire_share_links`         | Flip Active → Expired share links                |
| Daily    | `purge_expired_trash`        | Hard-delete docs in trash > 30 days              |
| Daily    | `process_account_deletions`  | Wipe accounts past the 7-day DPDP grace period   |
| Daily    | `reset_monthly_scan_counts`  | Roll `ai_scans_used_this_month` back to zero     |
| Weekly   | `send_storage_warnings`      | Email users >= 85% of their storage quota        |

## Permission model

- **`Docgettr User`** — standard role. Granted on signup. Read/write own
  data, read-only on catalogue.
- **`Docgettr Admin`** — operator role for support / refunds. Read/write
  all DocTypes; can override tiers and wipe accounts.
- **Row-level access** for documents is enforced in
  `utils/permissions.py:document_has_permission`. Family members can read
  shared docs based on the member's role (Admin / Editor / Viewer);
  `is_private=1` documents are always owner-only.

## What's intentionally out of scope here

- The frontend `FrappeDataService` swap — handled in a later session.
- Bench setup itself — assumed to be Frappe Cloud.
- Enriching the 100-type catalogue with every regex / hint — ~15 priority
  types (Aadhaar, PAN, Passport, DL, etc.) have full `fields_schema_json`;
  the rest carry a minimal schema and will be improved as real samples
  arrive.

## Handoff checklist for the next session

1. Push this repo to GitHub.
2. `bench get-app` it onto your Frappe Cloud bench; `install-app` on the
   site; `migrate` and `import-fixtures`.
3. Set the `site_config.json` keys above.
4. Share the site's base URL (e.g. `https://docgettr.frappe.cloud`) and
   we'll wire the frontend's `FrappeDataService` to it in a follow-up
   session.
