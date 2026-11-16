"""Deterministic fake LLMClient / EmbeddingClient for tests, no Ollama needed."""

import copy
import hashlib
import math
from collections.abc import Iterator
from typing import Any

from app.llm.base import ChatTurn, EmbeddingClient, LLMClient


class FakeLLMClient(LLMClient):
    """Returns a canned response, or echoes the prompt if none is queued.

    For the agent, `turns` scripts `chat_with_tools`: each call pops the next
    ChatTurn (or raises it, if it's an exception). `chat_calls` records what each
    call was sent, copied, so tests can check the messages and the tools offered.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        turns: list[ChatTurn | Exception] | None = None,
    ):
        self._responses = list(responses or [])
        self.calls: list[str] = []
        self.turns: list[ChatTurn | Exception] = list(turns or [])
        self.chat_calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192) -> str:
        self.calls.append(prompt)
        if self._responses:
            return self._responses.pop(0)
        return f"[fake response to: {prompt[:50]}]"

    def stream_generate(
        self, prompt: str, *, temperature: float = 0.2, num_ctx: int = 8192
    ) -> Iterator[str]:
        text = self.generate(prompt, temperature=temperature, num_ctx=num_ctx)
        words = text.split(" ")
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        num_ctx: int = 8192,
    ) -> ChatTurn:
        self.chat_calls.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if not self.turns:
            return ChatTurn(content="[fake answer]")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


class FakeEmbeddingClient(EmbeddingClient):
    """Hashes each text into a fixed-size, unit-normalized vector.

    Same text always yields the same vector, and vectors are stable across
    runs, so tests can assert on similarity/ordering without a real model.
    """

    def __init__(self, dimensions: int = 16):
        self._dimensions = dimensions
        self.call_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = []
        for i in range(self._dimensions):
            digest = hashlib.sha256(f"{i}:{text}".encode()).digest()
            vector.append(int.from_bytes(digest[:4], "big") / 2**32 - 0.5)
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    @property
    def dimensions(self) -> int:
        return self._dimensions
