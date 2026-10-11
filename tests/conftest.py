"""Fixtures. The admin is driven through real requests, not called directly.

Calling ``save_model`` by hand would test the mixin against an object graph the
admin never actually produces. Posting a form exercises the real sequence —
``save_model`` then ``save_related`` — which is where the interesting bugs are.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from tests.testapp.models import Article, Author, Tag


@pytest.fixture
def staff(db: None) -> User:
    return User.objects.create_superuser(
        "auditor", "auditor@example.com", "password-for-tests"
    )


@pytest.fixture
def admin_client_for(client: Client, staff: User) -> Client:
    client.force_login(staff)
    return client


@pytest.fixture
def author(db: None) -> Author:
    return Author.objects.create(name="Clarice")


@pytest.fixture
def tags(db: None) -> list[Tag]:
    return [Tag.objects.create(name=name) for name in ("fiction", "essay")]


@pytest.fixture
def article(author: Author) -> Article:
    return Article.objects.create(
        title="A Hora da Estrela",
        body="original body",
        published=False,
        price="19.90",
        author=author,
        api_token="super-secret-token",
    )
