"""The agent's tools, one module per domain, and the registry that holds them."""

from app.agent.registry import ToolRegistry
from app.agent.tools.business import business_tools
from app.agent.tools.charts import chart_tools
from app.agent.tools.documents import document_tools
from app.agent.tools.external import external_tools
from app.retrieval.library import DocumentLibrary


def build_registry(documents: DocumentLibrary | None = None) -> ToolRegistry:
    """Every tool the agent can call. `search_documents` is offered only when there
    is a document library with at least one shelf Luna can see. `fx_rate` is always
    offered: with external APIs off it answers with the stored rate (decision 0037)."""
    tools = business_tools() + external_tools() + chart_tools()
    shelves = documents.shelf_names() if documents is not None else []
    if shelves:
        tools = document_tools(shelves) + tools
    return ToolRegistry(tools)
