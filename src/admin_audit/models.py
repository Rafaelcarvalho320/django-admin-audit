"""The audit table.

Append-only by intention. There is no update path and no delete path in the
admin, because a log somebody can edit answers no question anybody asks it.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditEntry(models.Model):
    """One change made through the admin."""

    class Action(models.TextChoices):
        CREATE = "create", "Created"
        UPDATE = "update", "Updated"
        DELETE = "delete", "Deleted"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=10, choices=Action.choices)

    # SET_NULL, not CASCADE. Deleting a user must not delete the record of what
    # that user did, which is the one thing an audit log exists to prevent.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    # Kept as text beside the relation, so the entry still names who did it
    # after the account is gone.
    username = models.CharField(max_length=150, blank=True)

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="audit_entries"
    )
    # Text, so it fits an integer pk, a UUID or a natural key alike.
    object_id = models.CharField(max_length=255, db_index=True)
    target = GenericForeignKey("content_type", "object_id")
    # A snapshot of the object's text at the time. The object may be gone, or
    # renamed, and the log should still read like what happened.
    object_repr = models.CharField(max_length=255, blank=True)

    changes = models.JSONField(default=dict, blank=True)

    request_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "audit entry"
        verbose_name_plural = "audit entries"
        indexes = [
            # The query the admin and every investigation actually run: the
            # history of one object, newest first.
            models.Index(
                fields=["content_type", "object_id", "-created_at"],
                name="audit_object_history_idx",
            ),
        ]

    def __str__(self) -> str:
        who = self.username or "unknown"
        return f"{who} {self.action} {self.object_repr or self.object_id}"

    @property
    def changed_fields(self) -> list[str]:
        return sorted(self.changes)

    def change_for(self, field: str) -> dict[str, Any] | None:
        value = self.changes.get(field)
        return value if isinstance(value, dict) else None
