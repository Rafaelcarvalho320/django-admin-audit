"""Snapshots and diffs."""

from __future__ import annotations

import pytest

from admin_audit.diff import diff, m2m_snapshot, snapshot, snapshot_from_db
from tests.testapp.models import Article, Tag

pytestmark = pytest.mark.django_db


class TestDiff:
    def test_only_changed_fields_are_reported(self) -> None:
        changes = diff({"a": 1, "b": 2}, {"a": 1, "b": 3})
        assert changes == {"b": {"before": 2, "after": 3}}

    def test_identical_snapshots_produce_nothing(self) -> None:
        assert diff({"a": 1}, {"a": 1}) == {}

    def test_a_field_added_between_snapshots_shows_as_appearing(self) -> None:
        assert diff({}, {"a": 1}) == {"a": {"before": None, "after": 1}}

    def test_a_field_removed_shows_as_disappearing(self) -> None:
        assert diff({"a": 1}, {}) == {"a": {"before": 1, "after": None}}

    def test_the_output_is_ordered_by_field_name(self) -> None:
        """So two entries for the same change read the same way."""
        changes = diff({"z": 1, "a": 1}, {"z": 2, "a": 2})
        assert list(changes) == ["a", "z"]


class TestSnapshot:
    def test_it_captures_the_loggable_fields(self, article: Article) -> None:
        captured = snapshot(article)
        assert captured["title"] == "A Hora da Estrela"
        assert captured["published"] is False
        assert captured["price"] == "19.90"

    def test_the_surrogate_key_is_not_included(self, article: Article) -> None:
        """It never changes, so it would be noise in every entry."""
        assert "id" not in snapshot(article)

    def test_an_auto_now_field_is_not_included(self, article: Article) -> None:
        """It moves on every save and would make every entry look modified."""
        assert "updated_at" not in snapshot(article)

    def test_a_secret_field_is_captured_redacted(self, article: Article) -> None:
        assert snapshot(article)["api_token"] == "[redacted]"

    def test_m2m_is_excluded_unless_asked_for(self, article: Article) -> None:
        assert "tags" not in snapshot(article)
        assert "tags" in snapshot(article, include_m2m=True)


class TestSnapshotFromDb:
    def test_it_reads_the_stored_row_not_the_instance(self, article: Article) -> None:
        """The whole reason this function exists.

        By the time the admin calls save_model the instance already holds the
        new values, so the old ones only exist in the database.
        """
        article.title = "changed in memory"
        stored = snapshot_from_db(article)
        assert stored["title"] == "A Hora da Estrela"

    def test_an_unsaved_instance_has_no_stored_state(self, author: object) -> None:
        unsaved = Article(title="draft", author=author)  # type: ignore[arg-type]
        assert snapshot_from_db(unsaved) == {}

    def test_a_row_deleted_underneath_gives_an_empty_snapshot(
        self, article: Article
    ) -> None:
        pk = article.pk
        Article.objects.filter(pk=pk).delete()
        assert snapshot_from_db(article) == {}


class TestM2MSnapshot:
    def test_it_lists_the_related_keys(self, article: Article, tags: list[Tag]) -> None:
        article.tags.set(tags)
        captured = m2m_snapshot(article)
        assert captured["tags"] == sorted(tag.pk for tag in tags)

    def test_the_keys_are_sorted_so_order_is_not_a_change(
        self, article: Article, tags: list[Tag]
    ) -> None:
        """A related manager's ordering is not stable, and an unsorted
        comparison would invent a change on every save."""
        article.tags.set(tags)
        first = m2m_snapshot(article)
        article.tags.set(list(reversed(tags)))
        assert m2m_snapshot(article) == first

    def test_an_empty_relation_is_an_empty_list(self, article: Article) -> None:
        assert m2m_snapshot(article) == {"tags": []}
