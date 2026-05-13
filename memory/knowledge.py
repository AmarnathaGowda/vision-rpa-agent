"""ChromaDB long-term knowledge store — UI patterns and error recoveries.

`chromadb` is a Phase 4 dependency. Install with `poetry install --with phase4`
when ready to use. The module imports cleanly without it — only
`KnowledgeStore()` requires it at runtime.
"""
from __future__ import annotations
from config.settings import settings


class KnowledgeStore:
    def __init__(self) -> None:
        import chromadb  # deferred — see module docstring
        from chromadb.config import Settings as ChromaSettings
        self.client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.ui_patterns    = self.client.get_or_create_collection("ui_patterns")
        self.error_recovery = self.client.get_or_create_collection("error_recoveries")
        self.task_templates = self.client.get_or_create_collection("task_templates")

    def query_ui_pattern(self, app: str, element_desc: str) -> dict | None:
        raise NotImplementedError

    def query_error_recovery(self, error_text: str, app: str) -> dict | None:
        raise NotImplementedError

    def store_ui_pattern(self, app: str, element_desc: str,
                         selector: str, action_type: str) -> None:
        raise NotImplementedError

    def store_error_recovery(self, error_pattern: str, app: str,
                              recovery_action: str, succeeded: bool) -> None:
        raise NotImplementedError
