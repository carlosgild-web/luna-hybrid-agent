"""The agent's tools, one module per domain, and the registry that holds them."""

from app.agent.registry import ToolRegistry
from app.agent.tools.business import business_tools
from app.agent.tools.documents import document_tools
from app.retrieval.library import DocumentLibrary


def build_registry(documents: DocumentLibrary | None = None) -> ToolRegistry:
    """Every tool the agent can call. `search_documents` is offered only when there
    is a document library with at least one shelf Luna can see. Phase 5 adds FX and
    charts."""
    tools = business_tools()
    shelves = documents.shelf_names() if documents is not None else []
    if shelves:
        tools = document_tools(shelves) + tools
    return ToolRegistry(tools)
