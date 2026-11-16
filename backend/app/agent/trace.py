"""The agent trace: every LLM turn and tool call of one run, in order (invariant 8).

A tool step keeps `result` exactly as the model was shown it (`ToolResult.to_message()`),
so an answer can be rebuilt from its trace (invariant 11). The trace lives in memory
while the loop runs; `versioning/agent_traces.py` stores it.
"""

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Status = Literal[
    "running", "answered", "max_iterations", "token_budget", "tool_errors", "llm_error"
]


@dataclass
class LLMStep:
    """One call to the model. `tools_offered` is False for the final call that must
    answer in text; `parsed_from_content` marks tool calls the model wrote as JSON in
    its text instead of using the tools API (a qwen2.5 habit)."""

    iteration: int
    content: str
    tool_calls: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    ms: int
    tools_offered: bool = True
    parsed_from_content: bool = False
    kind: Literal["llm"] = "llm"


@dataclass
class ToolStep:
    """One tool call. `reused` means the same name and arguments were already called
    in this run and the earlier result was shown again instead of running it twice."""

    iteration: int
    name: str
    arguments: dict[str, Any]
    ok: bool
    result: str
    ms: int
    reused: bool = False
    kind: Literal["tool"] = "tool"


Step = LLMStep | ToolStep


@dataclass
class AgentTrace:
    question: str
    model: str
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    steps: list[Step] = field(default_factory=list)
    status: Status = "running"
    answer: str = ""
    error: str | None = None
    duration_ms: int = 0
    id: int | None = None  # set once stored

    @property
    def iterations(self) -> int:
        """LLM calls made."""
        return sum(isinstance(step, LLMStep) for step in self.steps)

    @property
    def prompt_tokens(self) -> int:
        return sum(step.prompt_tokens for step in self.steps if isinstance(step, LLMStep))

    @property
    def completion_tokens(self) -> int:
        return sum(step.completion_tokens for step in self.steps if isinstance(step, LLMStep))

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def tools_used(self) -> list[str]:
        """Tool names in the order first called."""
        names: list[str] = []
        for step in self.steps:
            if isinstance(step, ToolStep) and step.name not in names:
                names.append(step.name)
        return names

    def steps_as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(step) for step in self.steps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "status": self.status,
            "answer": self.answer,
            "error": self.error,
            "iterations": self.iterations,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "duration_ms": self.duration_ms,
            "steps": self.steps_as_dicts(),
        }
