from django.urls import path

from watcher.knowledge_base.api.views import (
    ask_knowledge_base,
)


urlpatterns = [
    path(
        "ask/",
        ask_knowledge_base,
        name="knowledge-base-ask",
    ),
]