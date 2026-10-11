"""Turning model field values into something JSON can hold.

Two jobs, and the second one is the important one.

The first is mechanical: a ``date``, a ``Decimal``, a ``UUID`` or a ``FileField``
cannot go into a ``JSONField`` as they are. They are converted to primitives
that survive a round trip and still read like the original value.

The second is refusal. An audit log is a table that accumulates forever, is
read by more people than the original record, and gets copied into backups and
staging dumps. A password hash, an API token or a card number must never enter
it — not redacted-later, never written. Redaction happens here, at the only
point where a value becomes a log entry, rather than being left to whoever adds
the next admin.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from django.db import models
from django.db.models.fields.files import FieldFile

#: Substrings that make a field name sensitive. Matched case-insensitively
#: against the field name, so ``password``, ``PasswordHash`` and
#: ``user_password_confirm`` are all caught.
SENSITIVE_NAME_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "cvv",
    "card_number",
    "security_question",
    "otp",
    "session",
)

#: What a redacted value looks like in the log. A fixed marker rather than the
#: value's length or hash: either would leak something about the secret.
REDACTED = "[redacted]"

#: Longer text is truncated. An audit row is a record of what changed, not a
#: second copy of the database, and a 2 MB description field in every entry
#: turns the log into the biggest table in the schema.
MAX_LENGTH = 500
TRUNCATION_SUFFIX = "... [truncated]"


def is_sensitive(field: models.Field[Any, Any]) -> bool:
    """Whether this field's value must never be written to the log.

    Checked by name *and* by type. The name catches the common cases; the type
    check catches a password stored in a field somebody called ``pw`` or
    ``hash``, which no name list would ever cover.
    """
    name = field.name.lower()
    if any(part in name for part in SENSITIVE_NAME_PARTS):
        return True
    # Django's own password field on the user model is a plain CharField, so
    # there is no type to recognise. What can be recognised is the field class
    # a project introduces for secrets; honour an explicit opt-out marker.
    return bool(getattr(field, "audit_sensitive", False))


def primitive(value: Any) -> Any:
    """Convert one value to something JSON can hold, as readably as possible."""
    if value is None or isinstance(value, bool | int | float | str):
        return _truncate(value) if isinstance(value, str) else value
    if isinstance(value, decimal.Decimal):
        # String, not float: a float would change the number on the way in,
        # which in an audit log is worse than useless.
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, FieldFile):
        return value.name or None
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, models.Model):
        return {"pk": primitive(value.pk), "repr": _truncate(str(value))}
    if isinstance(value, list | tuple | set):
        return [primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): primitive(item) for key, item in value.items()}
    return _truncate(str(value))


def _truncate(text: str) -> str:
    if len(text) <= MAX_LENGTH:
        return text
    return text[: MAX_LENGTH - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


def field_value(instance: models.Model, field: models.Field[Any, Any]) -> Any:
    """The loggable value of one field on one instance.

    Foreign keys are read by their raw id rather than followed. Following would
    emit a query per field per saved object, and the pk is the part that
    actually identifies what changed; the object's text is recorded separately
    in ``object_repr`` where one query is enough.
    """
    if is_sensitive(field):
        return REDACTED
    if isinstance(field, models.ForeignKey):
        return primitive(getattr(instance, field.attname))
    return primitive(getattr(instance, field.name, None))
