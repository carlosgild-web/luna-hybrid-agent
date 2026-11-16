"""A read-only view of the document library for the agent's `search_documents`
(PLAN_HYBRID.md Phase 4).

It runs the employee portal's retrieval (glossary expansion, hybrid vector + FTS5
search, the reranker when it's on) over the shelves Luna can see in the portal
(decision 0010), so the agent and the portal find the same passages. It only
selects; the tool handlers get this object instead of the database (invariant 9).
"""

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import Collection
from app.llm.base import EmbeddingClient
from app.retrieval.qa import prepare_context_many
from app.retrieval.reranker import Reranker
from app.retrieval.retrieve import RetrievedChunk
from app.retrieval.vector_store import VectorStore

if TYPE_CHECKING:
    from app.core.runtime import Runtime


@dataclass(frozen=True)
class DocumentLibrary:
    """What `search_documents` needs from the runtime, and nothing that writes."""

    settings: Settings
    session_factory: sessionmaker[Session]
    vector_store: VectorStore
    embedding_client_for: Callable[[str], EmbeddingClient]
    reranker: Reranker | None = None

    @classmethod
    def from_runtime(cls, runtime: "Runtime") -> "DocumentLibrary":
        return cls(
            settings=runtime.settings,
            session_factory=runtime.session_factory,
            vector_store=runtime.vector_store,
            embedding_client_for=runtime.embedding_client_for,
            reranker=runtime.reranker,
        )

    def shelf_names(self) -> list[str]:
        with self.session_factory() as db:
            return [shelf.name for shelf in _portal_shelves(db)]

    def search(self, query: str, shelf: str | None) -> "SearchResult":
        with self.session_factory() as db:
            shelves = _portal_shelves(db)
            if not shelves:
                return SearchResult([], [], note="The document library is empty.")
            chosen, note = _pick_shelves(shelves, shelf)
            context = prepare_context_many(
                db,
                query,
                collections=chosen,
                settings=self.settings,
                embedding_client_for=self.embedding_client_for,
                vector_store=self.vector_store,
                top_k=self.settings.retrieval_top_k,
                reranker=self.reranker,
            )
        chunks = context.chunks if context else []
        return SearchResult([c.name for c in chosen], chunks, note=note)


def _portal_shelves(db: Session) -> list[Collection]:
    """The shelves Luna searches: active and visible in the portal (decision 0010)."""
    return list(
        db.execute(
            select(Collection)
            .where(Collection.is_active.is_(True), Collection.portal_visible.is_(True))
            .order_by(Collection.name)
        ).scalars()
    )


@dataclass(frozen=True)
class SearchResult:
    shelves: list[str]
    chunks: list[RetrievedChunk]
    note: str = ""


def _key(name: str) -> str:
    """Shelf names compared the way a model writes them: "Customer Support" and
    "customer_support" both find "customer-support"."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "-".join(plain.replace("_", " ").replace("-", " ").split())


def _pick_shelves(shelves: list[Collection], wanted: str | None) -> tuple[list[Collection], str]:
    """One shelf when `wanted` names one (exactly, or as the start of a single
    shelf's name: "meeting" finds "meetings"). A name that matches nothing searches
    every shelf and says so, rather than failing: a small model can't fix a shelf
    name it has to guess."""
    if not wanted:
        return shelves, ""
    key = _key(wanted)
    exact = [s for s in shelves if _key(s.name) == key]
    if exact:
        return exact, ""
    partial = [s for s in shelves if _key(s.name).startswith(key) or key.startswith(_key(s.name))]
    if len(partial) == 1:
        return partial, ""
    names = ", ".join(s.name for s in shelves)
    return shelves, f"No shelf is called {wanted!r}, so every shelf was searched ({names})."
