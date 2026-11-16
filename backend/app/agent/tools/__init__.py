"""The agent's tools, one module per domain, and the registry that holds them."""

from app.agent.registry import ToolRegistry
from app.agent.tools.business import business_tools


def build_registry() -> ToolRegistry:
    """Every tool the agent can call. Later phases add documents, FX and charts."""
    return ToolRegistry(business_tools())
