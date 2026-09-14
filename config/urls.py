"""
URL configuration for config project.
"""

from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "api/knowledge-base/",
        include("watcher.knowledge_base.api.urls"),
    ),
]