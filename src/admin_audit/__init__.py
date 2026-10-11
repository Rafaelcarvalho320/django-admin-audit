"""django-admin-audit: record who changed what in the Django admin.

Add :class:`~admin_audit.mixins.AuditedModelAdmin` to a ``ModelAdmin`` and
every create, update and delete made through it is written to
:class:`~admin_audit.models.AuditEntry` with a before/after diff of the fields
that actually changed.

Nothing here is imported eagerly. A reusable Django app that touches models at
package-import time raises ``AppRegistryNotReady`` the moment anything imports
it before ``django.setup()`` has run — which ``makemigrations`` does. The
PEP 562 hook below gives the convenient ``from admin_audit import
AuditedModelAdmin`` without importing a single model until it is asked for.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

#: Public name -> the module it actually lives in.
_EXPORTS: dict[str, str] = {
    "AuditEntry": "admin_audit.models",
    "AuditedModelAdmin": "admin_audit.mixins",
    "record_change": "admin_audit.mixins",
    "diff": "admin_audit.diff",
    "snapshot": "admin_audit.diff",
    "snapshot_from_db": "admin_audit.diff",
    "REDACTED": "admin_audit.serialize",
    "is_sensitive": "admin_audit.serialize",
    "primitive": "admin_audit.serialize",
}

__all__ = ["__version__", *sorted(_EXPORTS)]


def __getattr__(name: str) -> Any:
    """Import a public name on first use. See the module docstring."""
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_path), name)
    # Cached in the module namespace, so the indirection costs one lookup.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
