from django.contrib import admin

from admin_audit import AuditedModelAdmin
from tests.testapp.models import Article, Author, Tag


@admin.register(Article)
class ArticleAdmin(AuditedModelAdmin):
    list_display = ["title", "author", "published"]


@admin.register(Author)
class AuthorAdmin(AuditedModelAdmin):
    list_display = ["name"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    """Not audited, so the tests can prove the mixin is what does the work."""

    list_display = ["name"]
