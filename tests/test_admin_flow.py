"""The mixin, driven through real admin requests.

Posting forms rather than calling save_model by hand: the interesting bugs live
in the sequence the admin actually performs, and m2m fields in particular are
written in a different hook from the object's own fields.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from admin_audit.models import AuditEntry
from tests.testapp.models import Article, Author, Tag

pytestmark = pytest.mark.django_db


def change_url(article: Article) -> str:
    return reverse("admin:testapp_article_change", args=[article.pk])


def form_data(article: Article, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "title": article.title,
        "body": article.body,
        "price": str(article.price),
        "author": article.author_id,
        "api_token": article.api_token,
        "tags": list(article.tags.values_list("pk", flat=True)),
        "published_on": "",
    }
    if article.published:
        data["published"] = "on"
    data.update(overrides)
    return data


class TestCreate:
    def test_adding_through_the_admin_is_recorded(
        self, admin_client_for: Client, author: Author
    ) -> None:
        response = admin_client_for.post(
            reverse("admin:testapp_article_add"),
            data={
                "title": "New article",
                "body": "",
                "price": "0.00",
                "author": author.pk,
                "api_token": "",
                "published_on": "",
                "tags": [],
            },
        )
        assert response.status_code == 302, response.content

        entry = AuditEntry.objects.get()
        assert entry.action == AuditEntry.Action.CREATE
        assert entry.object_repr == "New article"
        assert entry.changes["title"] == {"before": None, "after": "New article"}

    def test_the_actor_is_recorded_by_name_and_relation(
        self, admin_client_for: Client, author: Author, staff: User
    ) -> None:
        admin_client_for.post(
            reverse("admin:testapp_article_add"),
            data={
                "title": "Another",
                "body": "",
                "price": "0.00",
                "author": author.pk,
                "api_token": "",
                "published_on": "",
                "tags": [],
            },
        )
        entry = AuditEntry.objects.get()
        assert entry.user == staff
        assert entry.username == "auditor"


class TestUpdate:
    def test_only_the_changed_field_is_recorded(
        self, admin_client_for: Client, article: Article
    ) -> None:
        response = admin_client_for.post(
            change_url(article), data=form_data(article, title="Renamed")
        )
        assert response.status_code == 302, response.content

        entry = AuditEntry.objects.get()
        assert entry.action == AuditEntry.Action.UPDATE
        assert entry.changed_fields == ["title"]
        assert entry.changes["title"] == {
            "before": "A Hora da Estrela",
            "after": "Renamed",
        }

    def test_saving_without_changing_anything_records_nothing(
        self, admin_client_for: Client, article: Article
    ) -> None:
        """The admin saves the form either way, and a log full of
        'updated, no fields' entries is a log people stop reading."""
        admin_client_for.post(change_url(article), data=form_data(article))
        assert AuditEntry.objects.count() == 0

    def test_a_boolean_change_reads_as_a_boolean(
        self, admin_client_for: Client, article: Article
    ) -> None:
        admin_client_for.post(
            change_url(article), data=form_data(article, published="on")
        )
        entry = AuditEntry.objects.get()
        assert entry.changes["published"] == {"before": False, "after": True}

    def test_a_decimal_change_keeps_the_exact_amount(
        self, admin_client_for: Client, article: Article
    ) -> None:
        admin_client_for.post(
            change_url(article), data=form_data(article, price="24.50")
        )
        entry = AuditEntry.objects.get()
        assert entry.changes["price"] == {"before": "19.90", "after": "24.50"}

    def test_several_fields_at_once(
        self, admin_client_for: Client, article: Article
    ) -> None:
        admin_client_for.post(
            change_url(article),
            data=form_data(article, title="Both", body="new body"),
        )
        entry = AuditEntry.objects.get()
        assert entry.changed_fields == ["body", "title"]

    def test_the_auto_timestamp_is_never_reported(
        self, admin_client_for: Client, article: Article
    ) -> None:
        admin_client_for.post(
            change_url(article), data=form_data(article, title="Touched")
        )
        assert "updated_at" not in AuditEntry.objects.get().changes


class TestSecrets:
    def test_a_rotation_is_recorded_without_either_value(
        self, admin_client_for: Client, article: Article
    ) -> None:
        """That the credential moved is auditable. What it moved to is not.

        Both sides redact to the same marker, so an ordinary diff would report
        a rotated token as no change at all and lose the fact entirely.
        """
        admin_client_for.post(
            change_url(article), data=form_data(article, api_token="a-new-token")
        )
        entry = AuditEntry.objects.get()
        assert entry.changes == {
            "api_token": {"before": "[redacted]", "after": "[redacted]"}
        }

    def test_leaving_the_token_alone_is_not_a_change(
        self, admin_client_for: Client, article: Article
    ) -> None:
        admin_client_for.post(change_url(article), data=form_data(article))
        assert AuditEntry.objects.count() == 0

    def test_the_secret_never_reaches_the_table(
        self, admin_client_for: Client, article: Article
    ) -> None:
        admin_client_for.post(
            change_url(article),
            data=form_data(article, title="Renamed", api_token="a-new-token"),
        )
        stored = str(AuditEntry.objects.get().changes)
        assert "a-new-token" not in stored
        assert "super-secret-token" not in stored


class TestManyToMany:
    def test_adding_tags_on_create_lands_in_the_same_entry(
        self, admin_client_for: Client, author: Author, tags: list[Tag]
    ) -> None:
        """One click, one entry. m2m is written in a later hook than the
        object's own fields, which used to produce two rows."""
        admin_client_for.post(
            reverse("admin:testapp_article_add"),
            data={
                "title": "Tagged",
                "body": "",
                "price": "0.00",
                "author": author.pk,
                "api_token": "",
                "published_on": "",
                "tags": [tag.pk for tag in tags],
            },
        )
        entry = AuditEntry.objects.get()
        assert entry.action == AuditEntry.Action.CREATE
        assert entry.changes["tags"]["after"] == sorted(t.pk for t in tags)

    def test_creating_without_tags_is_not_a_second_entry(
        self, admin_client_for: Client, author: Author
    ) -> None:
        admin_client_for.post(
            reverse("admin:testapp_article_add"),
            data={
                "title": "Untagged",
                "body": "",
                "price": "0.00",
                "author": author.pk,
                "api_token": "",
                "published_on": "",
                "tags": [],
            },
        )
        assert AuditEntry.objects.count() == 1

    def test_changing_only_tags_is_recorded(
        self, admin_client_for: Client, article: Article, tags: list[Tag]
    ) -> None:
        """Nothing else moved, so there is no earlier entry to merge into."""
        admin_client_for.post(
            change_url(article), data=form_data(article, tags=[tags[0].pk])
        )
        entry = AuditEntry.objects.get()
        assert entry.action == AuditEntry.Action.UPDATE
        assert entry.changes == {"tags": {"before": [], "after": [tags[0].pk]}}

    def test_removing_a_tag_is_recorded(
        self, admin_client_for: Client, article: Article, tags: list[Tag]
    ) -> None:
        article.tags.set(tags)
        admin_client_for.post(
            change_url(article), data=form_data(article, tags=[tags[0].pk])
        )
        entry = AuditEntry.objects.get()
        assert entry.changes["tags"]["before"] == sorted(t.pk for t in tags)
        assert entry.changes["tags"]["after"] == [tags[0].pk]

    def test_resaving_the_same_tags_is_not_a_change(
        self, admin_client_for: Client, article: Article, tags: list[Tag]
    ) -> None:
        article.tags.set(tags)
        admin_client_for.post(change_url(article), data=form_data(article))
        assert AuditEntry.objects.count() == 0

    def test_a_field_and_its_tags_changing_together_is_one_entry(
        self, admin_client_for: Client, article: Article, tags: list[Tag]
    ) -> None:
        admin_client_for.post(
            change_url(article),
            data=form_data(article, title="Both", tags=[tags[1].pk]),
        )
        entry = AuditEntry.objects.get()
        assert entry.changed_fields == ["tags", "title"]


class TestDelete:
    def delete_one(self, client: Client, article: Article) -> object:
        return client.post(
            reverse("admin:testapp_article_delete", args=[article.pk]),
            data={"post": "yes"},
        )

    def test_deleting_records_the_final_state(
        self, admin_client_for: Client, article: Article
    ) -> None:
        response = self.delete_one(admin_client_for, article)
        assert response.status_code == 302, response.content

        entry = AuditEntry.objects.get()
        assert entry.action == AuditEntry.Action.DELETE
        assert entry.object_repr == "A Hora da Estrela"
        assert entry.changes["title"]["before"] == "A Hora da Estrela"
        assert entry.changes["title"]["after"] is None

    def test_the_secret_is_still_redacted_on_delete(
        self, admin_client_for: Client, article: Article
    ) -> None:
        self.delete_one(admin_client_for, article)
        assert "super-secret-token" not in str(AuditEntry.objects.get().changes)

    def test_the_bulk_action_records_every_object(
        self, admin_client_for: Client, author: Author
    ) -> None:
        """The bulk action never calls delete_model, and overriding only the
        single-object hook is how a log misses everything removed by dropdown."""
        articles = [
            Article.objects.create(title=f"Article {n}", author=author)
            for n in range(3)
        ]
        response = admin_client_for.post(
            reverse("admin:testapp_article_changelist"),
            data={
                "action": "delete_selected",
                "_selected_action": [a.pk for a in articles],
                "post": "yes",
            },
        )
        assert response.status_code == 302, response.content
        assert Article.objects.count() == 0
        assert AuditEntry.objects.filter(action="delete").count() == 3

    def test_the_entry_outlives_the_object(
        self, admin_client_for: Client, article: Article
    ) -> None:
        self.delete_one(admin_client_for, article)
        entry = AuditEntry.objects.get()
        assert entry.target is None
        assert entry.object_repr == "A Hora da Estrela"


class TestUnauditedAdmin:
    def test_a_plain_model_admin_records_nothing(
        self, admin_client_for: Client
    ) -> None:
        """Proves the mixin does the work, not some global signal."""
        admin_client_for.post(
            reverse("admin:testapp_tag_add"), data={"name": "unaudited"}
        )
        assert Tag.objects.filter(name="unaudited").exists()
        assert AuditEntry.objects.count() == 0


class TestResilience:
    def test_a_failure_to_log_does_not_lose_the_change(
        self,
        admin_client_for: Client,
        article: Article,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An admin that stops working when the audit table is full is worse
        than one that saves the change and logs the problem."""
        from admin_audit import mixins

        def explode(**kwargs: object) -> None:
            raise RuntimeError("audit table is full")

        monkeypatch.setattr(mixins, "record_change", explode)
        response = admin_client_for.post(
            change_url(article), data=form_data(article, title="Saved anyway")
        )
        assert response.status_code == 302
        article.refresh_from_db()
        assert article.title == "Saved anyway"
        assert AuditEntry.objects.count() == 0


class TestActorDetails:
    def test_the_client_address_is_recorded(
        self, admin_client_for: Client, article: Article
    ) -> None:
        admin_client_for.post(
            change_url(article),
            data=form_data(article, title="From somewhere"),
            REMOTE_ADDR="203.0.113.7",
        )
        assert AuditEntry.objects.get().request_ip == "203.0.113.7"

    def test_a_malformed_forwarded_header_is_dropped_not_stored(
        self, admin_client_for: Client, article: Article
    ) -> None:
        """The header is client-controlled and the column is typed."""
        admin_client_for.post(
            change_url(article),
            data=form_data(article, title="Spoofed"),
            HTTP_X_FORWARDED_FOR="not-an-address",
        )
        assert AuditEntry.objects.get().request_ip is None

    def test_the_entry_survives_the_user_being_deleted(
        self, admin_client_for: Client, article: Article, staff: User
    ) -> None:
        """The one thing an audit log exists to prevent."""
        admin_client_for.post(
            change_url(article), data=form_data(article, title="Then gone")
        )
        staff.delete()

        entry = AuditEntry.objects.get()
        assert entry.user is None
        assert entry.username == "auditor"
