"""Turning values into something a JSONField can hold, and refusing secrets."""

from __future__ import annotations

import datetime
import decimal
import uuid

import pytest

from admin_audit.serialize import (
    MAX_LENGTH,
    REDACTED,
    field_value,
    is_sensitive,
    primitive,
)
from tests.testapp.models import Article


class TestPrimitive:
    def test_plain_values_pass_through(self) -> None:
        assert primitive(1) == 1
        assert primitive(True) is True
        assert primitive(None) is None
        assert primitive("text") == "text"

    def test_a_decimal_becomes_a_string_not_a_float(self) -> None:
        """A float would change the number, which in an audit log is worse
        than storing nothing at all."""
        value = primitive(decimal.Decimal("19.90"))
        assert value == "19.90"
        assert isinstance(value, str)

    def test_dates_and_times_become_iso(self) -> None:
        assert primitive(datetime.date(2026, 3, 4)) == "2026-03-04"
        assert primitive(datetime.datetime(2026, 3, 4, 12, 30)).startswith(
            "2026-03-04T12:30"
        )
        assert primitive(datetime.time(9, 5)) == "09:05:00"

    def test_a_timedelta_becomes_seconds(self) -> None:
        assert primitive(datetime.timedelta(minutes=2)) == 120.0

    def test_a_uuid_becomes_its_text(self) -> None:
        value = uuid.uuid4()
        assert primitive(value) == str(value)

    def test_bytes_become_a_size_not_their_content(self) -> None:
        assert primitive(b"\x00\x01\x02") == "<3 bytes>"

    def test_collections_are_converted_item_by_item(self) -> None:
        assert primitive([decimal.Decimal("1.5"), None]) == ["1.5", None]
        assert primitive({"a": decimal.Decimal("2")}) == {"a": "2"}

    def test_long_text_is_truncated(self) -> None:
        """An audit row records what changed; it is not a second copy of the
        database, and a megabyte of body text in every entry would make the log
        the largest table in the schema."""
        value = primitive("x" * (MAX_LENGTH * 2))
        assert len(value) == MAX_LENGTH
        assert value.endswith("[truncated]")

    def test_text_at_the_limit_is_left_alone(self) -> None:
        assert primitive("x" * MAX_LENGTH) == "x" * MAX_LENGTH


class TestSensitiveFields:
    @pytest.mark.parametrize(
        "name",
        [
            "password",
            "Password",
            "password_hash",
            "api_token",
            "APIKey",
            "client_secret",
            "private_key",
            "card_number",
            "cvv",
            "otp_code",
        ],
    )
    def test_names_that_must_never_be_logged(self, name: str) -> None:
        from django.db import models

        assert is_sensitive(models.CharField(name=name))

    @pytest.mark.parametrize("name", ["title", "body", "author", "tokenizer"])
    def test_ordinary_names_are_fine(self, name: str) -> None:
        from django.db import models

        # "tokenizer" contains "token" and is caught too. That is the right
        # trade: a false positive costs one unlogged field, a false negative
        # writes a credential into a table that lives forever.
        from admin_audit.serialize import SENSITIVE_NAME_PARTS

        expected = any(part in name for part in SENSITIVE_NAME_PARTS)
        assert is_sensitive(models.CharField(name=name)) is expected

    def test_a_field_can_opt_in_explicitly(self) -> None:
        from django.db import models

        field = models.CharField(name="pw")
        assert not is_sensitive(field)
        field.audit_sensitive = True  # type: ignore[attr-defined]
        assert is_sensitive(field)

    def test_the_marker_is_fixed_and_leaks_nothing(self) -> None:
        """Not the length, not a hash: either would say something about it."""
        assert REDACTED == "[redacted]"


@pytest.mark.django_db
class TestFieldValue:
    def test_a_secret_field_is_redacted_not_stored(self, article: Article) -> None:
        field = Article._meta.get_field("api_token")
        assert article.api_token == "super-secret-token"
        assert field_value(article, field) == REDACTED  # type: ignore[arg-type]

    def test_a_foreign_key_is_read_by_id_without_a_query(
        self, article: Article, django_assert_num_queries: object
    ) -> None:
        """Following it would cost a query per field per saved object."""
        field = Article._meta.get_field("author")
        with django_assert_num_queries(0):  # type: ignore[operator]
            assert field_value(article, field) == article.author_id  # type: ignore[arg-type]
