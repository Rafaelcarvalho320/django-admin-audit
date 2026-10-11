"""Models covering the cases the audit layer has to handle."""

from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=40)

    def __str__(self) -> str:
        return self.name


class Author(models.Model):
    name = models.CharField(max_length=80)

    def __str__(self) -> str:
        return self.name


class Article(models.Model):
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True)
    published = models.BooleanField(default=False)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    published_on = models.DateField(null=True, blank=True)
    author = models.ForeignKey(
        Author, on_delete=models.CASCADE, related_name="articles"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="articles")
    # Deliberately named so the redaction list catches it.
    api_token = models.CharField(max_length=64, blank=True)
    # A timestamp that moves on every save, which must not read as a change.
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title
