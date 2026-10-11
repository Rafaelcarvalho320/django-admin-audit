"""Snapshotting an instance and comparing two snapshots.

A snapshot is a plain ``dict`` of loggable values, which is what makes the diff
trivial and the tests readable. Two details matter more than the comparison
itself.

**Where the "before" comes from.** By the time the admin calls ``save_model``,
the instance in memory already holds the new values; the old ones only exist in
the database. The snapshot has to be taken by re-reading the row by primary
key, and the extra query is the price of having an audit log that is true.

**Many-to-many fields are not on the instance.** They live in a join table and
are only written after the object exists, which is why the admin writes them in
``save_related`` and not in ``save_model``. Diffing them at save time would
report every m2m change as "no change", so they are snapshotted separately and
compared at the point they are actually saved.
"""

from __future__ import annotations

from typing import Any

from django.db import models

from admin_audit.serialize import REDACTED, field_value, is_sensitive, primitive

#: Fields never worth logging: the surrogate key never changes, and an
#: auto-updated timestamp changes on every single save, which would make every
#: entry look like a modification even when nothing else moved.
ALWAYS_EXCLUDED: tuple[str, ...] = ("id", "pk")

Snapshot = dict[str, Any]


def concrete_fields(model: type[models.Model]) -> list[models.Field[Any, Any]]:
    """Editable, stored fields. Reverse relations and m2m are handled apart."""
    return [
        field
        for field in model._meta.concrete_fields
        if field.name not in ALWAYS_EXCLUDED
        and not getattr(field, "auto_now", False)
        and not getattr(field, "auto_now_add", False)
    ]


def m2m_fields(model: type[models.Model]) -> list[models.ManyToManyField[Any, Any]]:
    return list(model._meta.many_to_many)


def snapshot(instance: models.Model, *, include_m2m: bool = False) -> Snapshot:
    """Every loggable field value of ``instance``, as primitives."""
    data: Snapshot = {
        field.name: field_value(instance, field)
        for field in concrete_fields(type(instance))
    }
    if include_m2m and instance.pk is not None:
        data.update(m2m_snapshot(instance))
    return data


def m2m_snapshot(instance: models.Model) -> Snapshot:
    """The related primary keys of each m2m field, sorted for comparability.

    Sorted because the order a related manager returns rows in is not stable,
    and an unsorted comparison would invent changes on every save.
    """
    return {
        field.name: sorted(
            primitive(pk)
            for pk in getattr(instance, field.name).values_list("pk", flat=True)
        )
        for field in m2m_fields(type(instance))
    }


def stored_instance(instance: models.Model) -> models.Model | None:
    """Re-read the row as the database currently holds it.

    By the time the admin calls ``save_model`` the instance in hand already
    carries the new values, so the old ones exist only in the database. The
    extra query is the price of an audit log that is true.
    """
    if instance.pk is None:
        return None
    return type(instance)._default_manager.filter(pk=instance.pk).first()


def snapshot_from_db(instance: models.Model, *, include_m2m: bool = True) -> Snapshot:
    """Snapshot the row as it is stored, not as the instance now holds it.

    Returns an empty snapshot when the row is not there, which is the honest
    answer for an object being created.
    """
    stored = stored_instance(instance)
    if stored is None:
        return {}
    return snapshot(stored, include_m2m=include_m2m)


def sensitive_diff(
    before: models.Model, after: models.Model
) -> dict[str, dict[str, Any]]:
    """Secret fields that changed, recorded without either value.

    Needed because both sides of a redacted field snapshot to the same marker,
    so an ordinary diff reports a rotated token as no change at all. That loses
    a fact worth auditing - *that* the credential moved - while protecting
    nothing extra, since the values are still never written.

    The comparison happens here, in memory, against the real values. A hash
    would make the diff work without this function and is exactly what must
    not be stored: a weak secret falls to an offline guess against it.
    """
    changes: dict[str, dict[str, Any]] = {}
    for field in concrete_fields(type(after)):
        if not is_sensitive(field):
            continue
        name = field.attname if isinstance(field, models.ForeignKey) else field.name
        if getattr(before, name, None) != getattr(after, name, None):
            changes[field.name] = {"before": REDACTED, "after": REDACTED}
    return changes


def diff(before: Snapshot, after: Snapshot) -> dict[str, dict[str, Any]]:
    """Fields whose value changed, as ``{field: {"before": x, "after": y}}``.

    A field present on only one side is reported with ``None`` for the missing
    side, which is what happens when a model gains a field between the two
    snapshots.
    """
    changes: dict[str, dict[str, Any]] = {}
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old != new:
            changes[name] = {"before": old, "after": new}
    return changes
