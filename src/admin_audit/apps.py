from django.apps import AppConfig


class AdminAuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "admin_audit"
    verbose_name = "Admin audit"
