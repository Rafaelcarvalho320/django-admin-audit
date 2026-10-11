"""The admin mixin that does the recording.

Four hooks, because the admin writes an object in more than one step and a
mixin that only overrides ``save_model`` misses most of what happened:

``save_model``
    the object's own fields. The "before" has to be re-read from the database,
    since the instance in hand already holds the new values.
``save_related``
    many-to-many fields and inline formsets, which are written here and not in
    ``save_model``. Diffing them earlier reports every change as no change.
``delete_model``
    one deletion, snapshotted before the row goes.
``delete_queryset``
    the bulk action, which never calls ``delete_model`` at all. Overriding only
    the single-object hook is how an audit log misses the ten thousand rows
    somebody removed with a dropdown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest

from admin_audit.diff import (
    Snapshot,
    diff,
    m2m_snapshot,
    sensitive_diff,
    snapshot,
    stored_instance,
)
from admin_audit.models import AuditEntry
from admin_audit.serialize import primitive

if TYPE_CHECKING:
    # Subscripted only for the type checker. Doing it at runtime needs
    # django-stubs-ext's monkeypatch, and a reusable app cannot assume the
    # project installing it calls that - the import would simply raise
    # TypeError the moment the admin is autodiscovered.
    _ModelAdminBase = admin.ModelAdmin[Any]
else:
    _ModelAdminBase = admin.ModelAdmin


#: Where state is parked between save_model and save_related. On the request,
#: not on the admin instance: a ModelAdmin is created once per process and
#: shared by every request, so state stored on it would leak between
#: concurrent users.
_SNAPSHOT_ATTR = "_admin_audit_snapshot"
_ENTRY_ATTR = "_admin_audit_entry_pk"


def client_ip(request: HttpRequest) -> str | None:
    """The client address, preferring the proxy header when there is one.

    Only the first entry of X-Forwarded-For is read, and only if it parses.
    The header is client-controlled, so a malformed value is dropped rather
    than stored.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidate = forwarded.split(",")[0].strip() or str(
        request.META.get("REMOTE_ADDR") or ""
    )
    if not candidate:
        return None
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_ipv46_address

    try:
        validate_ipv46_address(candidate)
    except ValidationError:
        return None
    return candidate


def record_change(
    *,
    instance: models.Model,
    action: str,
    changes: dict[str, dict[str, Any]] | None = None,
    request: HttpRequest | None = None,
) -> AuditEntry | None:
    """Write one entry. Returns ``None`` when there was nothing to record.

    An update with an empty diff writes nothing on purpose: the admin saves a
    form whether or not anything changed, and a log full of "updated, no
    fields" entries is a log people stop reading.
    """
    if action == AuditEntry.Action.UPDATE and not changes:
        return None

    user = getattr(request, "user", None)
    authenticated = bool(user and getattr(user, "is_authenticated", False))

    return AuditEntry.objects.create(
        action=action,
        user=user if authenticated else None,
        username=str(getattr(user, "get_username", lambda: "")()) if user else "",
        content_type=ContentType.objects.get_for_model(
            instance, for_concrete_model=False
        ),
        object_id=str(primitive(instance.pk)),
        object_repr=str(instance)[:255],
        changes=changes or {},
        request_ip=client_ip(request) if request else None,
    )


class AuditedModelAdmin(_ModelAdminBase):
    """Mix into a ``ModelAdmin`` to record everything it changes.

    Recording is best-effort by design: a failure to write the log must not
    roll back the change the user actually asked for. The exception is logged
    and swallowed, because the alternative is an admin that stops working when
    the audit table is full.
    """

    audit_enabled = True

    # -- creates and updates ---------------------------------------------

    def save_model(
        self, request: HttpRequest, obj: Any, form: Any, change: bool
    ) -> None:
        # The stored row is read once and used twice: for the ordinary diff,
        # and to notice a secret changing without ever recording its value.
        stored = stored_instance(obj) if change else None
        before: Snapshot = (
            snapshot(stored, include_m2m=True) if stored is not None else {}
        )
        super().save_model(request, obj, form, change)
        if not self.audit_enabled:
            return

        after = snapshot(obj, include_m2m=False)
        if change:
            changes = diff({k: v for k, v in before.items() if k in after}, after)
            if stored is not None:
                changes.update(sensitive_diff(stored, obj))
        else:
            changes = {
                name: {"before": None, "after": value} for name, value in after.items()
            }

        # Carried to save_related, which is where m2m values are written and
        # where the entry is completed rather than duplicated.
        setattr(request, _SNAPSHOT_ATTR, before)
        setattr(request, _ENTRY_ATTR, None)

        entry = self._safely(
            record_change,
            instance=obj,
            action=(AuditEntry.Action.UPDATE if change else AuditEntry.Action.CREATE),
            changes=changes,
            request=request,
        )
        if entry is not None:
            setattr(request, _ENTRY_ATTR, entry.pk)

    def save_related(
        self,
        request: HttpRequest,
        form: Any,
        formsets: list[BaseInlineFormSet[Any, Any, Any]],
        change: bool,
    ) -> None:
        before: Snapshot = getattr(request, _SNAPSHOT_ATTR, {})
        super().save_related(request, form, formsets, change)
        if not self.audit_enabled:
            return

        instance = form.instance
        after = m2m_snapshot(instance)
        # An object that did not exist had no relations, so the absent value is
        # an empty list and not None. Treating it as None reported every
        # creation as also having changed its m2m fields, which produced a
        # second entry for one click.
        relevant_before = {name: before.get(name) or [] for name in after}
        changes = diff(relevant_before, after)
        if not changes:
            return

        entry_pk = getattr(request, _ENTRY_ATTR, None)
        if entry_pk is not None:
            # One user action, one entry. The m2m fields are merged into the
            # row save_model already wrote rather than appended as a separate
            # "update" nobody performed.
            self._safely(_merge_changes, entry_pk=entry_pk, changes=changes)
            return

        # Nothing else changed, so there is no entry to merge into: the user
        # only touched the relations.
        self._safely(
            record_change,
            instance=instance,
            action=AuditEntry.Action.UPDATE,
            changes=changes,
            request=request,
        )

    # -- deletions --------------------------------------------------------

    def delete_model(self, request: HttpRequest, obj: Any) -> None:
        captured = snapshot(obj, include_m2m=True)
        self._safely(
            record_change,
            instance=obj,
            action=AuditEntry.Action.DELETE,
            changes={
                name: {"before": value, "after": None}
                for name, value in captured.items()
            },
            request=request,
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request: HttpRequest, queryset: Any) -> None:
        """The bulk action, which never calls ``delete_model``.

        Each object is snapshotted before anything is removed, so a cascade
        cannot take a related row away mid-loop and leave a half-read entry.
        """
        if self.audit_enabled:
            pending = [(obj, snapshot(obj, include_m2m=True)) for obj in queryset]
            for obj, captured in pending:
                self._safely(
                    record_change,
                    instance=obj,
                    action=AuditEntry.Action.DELETE,
                    changes={
                        name: {"before": value, "after": None}
                        for name, value in captured.items()
                    },
                    request=request,
                )
        super().delete_queryset(request, queryset)

    # -- plumbing ---------------------------------------------------------

    def _safely(self, func: Any, **kwargs: Any) -> Any:
        import logging

        try:
            return func(**kwargs)
        except Exception:
            logging.getLogger("admin_audit").exception(
                "failed to write an audit entry; the change itself was saved"
            )
            return None


def _merge_changes(*, entry_pk: Any, changes: dict[str, dict[str, Any]]) -> None:
    """Add fields to an entry written earlier in the same request."""
    entry = AuditEntry.objects.filter(pk=entry_pk).first()
    if entry is None:
        return
    entry.changes = {**entry.changes, **changes}
    entry.save(update_fields=["changes"])
