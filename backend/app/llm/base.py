"""Abstract interfaces for chat and embedding clients.

Real implementations call Ollama; fake implementations are deterministic
and used by unit tests / CI so they never need Ollama running.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked for. `arguments` is what the model sent:
    usually a dict, sometimes a JSON string; the tool registry parses either."""

    name: str
    arguments: dict[str, Any] | str | None = None


@dataclass(frozen=True)
class ChatTurn:
    """One reply of a chat with tools: text, tool calls, or both, plus Ollama's
    token counts (0 when the server doesn't report them)."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192) -> str:
        """Generate a single completion for `prompt`."""

    @abstractmethod
    def stream_generate(
        self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192
    ) -> Iterator[str]:
        """Generate a completion for `prompt`, yielding text fragments as produced."""

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        num_ctx: int = 8192,
    ) -> ChatTurn:
        """One non-streaming chat turn. `messages` use Ollama's chat format (role,
        content, tool_calls, tool_name); `tools` is Ollama's `tools` list, and an
        empty list means the model must answer in text."""
        raise NotImplementedError(f"{type(self).__name__} doesn't support tool calling")


class EmbeddingClient(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, same order."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector dimensionality produced by this client."""
