"""Reading the log back, in the admin.

An audit log nobody can read is a table that costs disk and answers nothing, so
the entries get a proper list view, useful filters and a rendered diff. All of
it is read-only: a log somebody can edit answers no question anybody asks it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from admin_audit.models import AuditEntry

if TYPE_CHECKING:
    # Subscripted only for the type checker. Doing it at runtime needs
    # django-stubs-ext's monkeypatch, and a reusable app cannot assume the
    # project installing it calls that - the import would simply raise
    # TypeError the moment the admin is autodiscovered.
    _ModelAdminBase = admin.ModelAdmin[AuditEntry]
else:
    _ModelAdminBase = admin.ModelAdmin


@admin.register(AuditEntry)
class AuditEntryAdmin(_ModelAdminBase):
    list_display = [
        "created_at",
        "username",
        "action",
        "content_type",
        "object_repr",
        "summary",
    ]
    list_filter = ["action", "content_type", "created_at"]
    search_fields = ["username", "object_repr", "object_id"]
    date_hierarchy = "created_at"
    list_select_related = ["content_type", "user"]
    readonly_fields = [
        "created_at",
        "action",
        "user",
        "username",
        "content_type",
        "object_id",
        "object_repr",
        "request_ip",
        "rendered_changes",
    ]
    exclude = ["changes"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: AuditEntry | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: AuditEntry | None = None
    ) -> bool:
        """Not deletable through the admin.

        Trimming an audit log is a real operational need, and it belongs in a
        retention job with a documented policy, not behind a checkbox next to
        the rows it would remove.
        """
        return False

    @admin.display(description="changed")
    def summary(self, obj: AuditEntry) -> str:
        fields = obj.changed_fields
        if not fields:
            return "-"
        if len(fields) <= 3:
            return ", ".join(fields)
        return f"{', '.join(fields[:3])} (+{len(fields) - 3})"

    @admin.display(description="changes")
    def rendered_changes(self, obj: AuditEntry) -> SafeString:
        """A before/after table.

        Built with format_html rather than a template so the app stays a single
        importable package with nothing to add to TEMPLATES. Every value goes
        through Django's escaping: these strings came from user input and an
        audit log is the last place that should become an injection route.
        """
        if not obj.changes:
            return format_html("<em>no field changes recorded</em>")

        rows = format_html_join(
            "",
            "<tr>"
            '<th style="text-align:left;padding:2px 12px 2px 0">{}</th>'
            '<td style="padding:2px 12px 2px 0"><code>{}</code></td>'
            '<td style="padding:2px 0"><code>{}</code></td>'
            "</tr>",
            (
                (
                    name,
                    _display(change.get("before")),
                    _display(change.get("after")),
                )
                for name, change in sorted(obj.changes.items())
                if isinstance(change, dict)
            ),
        )
        return format_html(
            "<table>"
            '<tr><th style="text-align:left">field</th>'
            '<th style="text-align:left">before</th>'
            '<th style="text-align:left">after</th></tr>'
            "{}</table>",
            rows,
        )


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, dict) and "repr" in value:
        return f"{value['repr']} (#{value.get('pk')})"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "—"
    if value == "":
        return "(empty)"
    return str(value)
