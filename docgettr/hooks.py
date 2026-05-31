from . import __version__ as app_version  # noqa: F401

app_name = "docgettr"
app_title = "Docgettr"
app_publisher = "The Tiny App Company"
app_description = "Family document management for Indian households"
app_email = "Apps@thetinyappscompany.com"
app_license = "MIT"
required_apps = ["frappe"]

# Fixtures (seed data)
fixtures = [
    {"dt": "Docgettr Category", "filters": []},
    {"dt": "Docgettr Document Type", "filters": []},
    {"dt": "Role", "filters": [["name", "in", ["Docgettr User", "Docgettr Admin"]]]},
]

# Scheduler events
scheduler_events = {
    "hourly": [
        "docgettr.tasks.fire_pending_reminders",
    ],
    "daily": [
        "docgettr.tasks.purge_expired_trash",
        "docgettr.tasks.expire_share_links",
        "docgettr.tasks.process_account_deletions",
        "docgettr.tasks.reset_monthly_scan_counts",
        "docgettr.tasks.purge_stale_otps",
    ],
    "weekly": [
        "docgettr.tasks.send_storage_warnings",
    ],
}

# Doc Events
doc_events = {
    "Docgettr Document": {
        "after_insert": "docgettr.docgettr.utils.hooks_helpers.on_document_created",
        "on_trash": "docgettr.docgettr.utils.hooks_helpers.on_document_deleted",
    }
}

# Custom permission hooks
has_permission = {
    "Docgettr Document": "docgettr.docgettr.utils.permissions.document_has_permission",
    "Docgettr Share Link": "docgettr.docgettr.utils.permissions.share_link_has_permission",
}

# Jinja helpers for email templates
jinja = {
    "methods": [
        "docgettr.docgettr.utils.jinja_helpers.format_indian_date",
    ]
}

# After install
after_install = "docgettr.install.after_install"

# Website route rules — public share link page
website_route_rules = [
    {"from_route": "/share/<token>", "to_route": "share"},
]
