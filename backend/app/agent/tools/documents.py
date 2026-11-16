"""`search_documents`: the company's documents as a tool (PLAN_HYBRID.md Phase 4).

The search itself is `retrieval/library.py`: the employee portal's retrieval over
the shelves Luna can see in the portal, so the agent and the portal find the same
passages.

Passages are numbered for the whole run through `ToolContext.citations`: "[3]" is
one passage however many searches the model makes, and the answer cites it that
way. The model sees the number, the document and the text. The trace also keeps
the chunk, version and score behind each number in the step's `meta`, for the
message sources a chat answer stores (invariant 5).
"""

from typing import Any

from pydantic import Field

from app.agent.registry import Tool, ToolArgs, ToolContext, ToolError, ToolOutput

NO_PASSAGES = "Nothing in the documents matches this search."


# ------------------------------------------------------------------ the tool


class SearchDocumentsArgs(ToolArgs):
    query: str = Field(
        ...,
        min_length=2,
        description="What to look for, in a few words, e.g. 'Q3 sales meeting CH-2004 units'",
    )
    shelf: str | None = Field(
        None,
        description="Search only this shelf. Leave out to search every shelf.",
    )


def search_documents(args: SearchDocumentsArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.documents is None:
        raise ToolError("The document library isn't available here.")
    found = ctx.documents.search(args.query, args.shelf)

    passages: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    for chunk in found.chunks:
        number = ctx.citations.number(chunk.chunk_id)
        passage: dict[str, Any] = {"cite": f"[{number}]", "document": chunk.filename}
        where = f"page {chunk.page}" if chunk.page else chunk.heading
        if where:
            passage["where"] = where
        passage["text"] = chunk.text
        passages.append(passage)
        meta.append(
            {
                "number": number,
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "version_id": chunk.version_id,
                "filename": chunk.filename,
                "page": chunk.page,
                "heading": chunk.heading,
                "score": chunk.score,
            }
        )

    content: dict[str, Any] = {"query": args.query, "shelves": found.shelves}
    notes = [n for n in (found.note, "" if passages else NO_PASSAGES) if n]
    if notes:
        content["note"] = " ".join(notes)
    content["passages"] = passages
    return ToolOutput(content, meta={"passages": meta})


DESCRIPTION = (
    "Search the company's documents: policies, procedures, supplier agreements, "
    "product guides and meeting notes. Returns numbered passages; cite a passage in "
    "your answer by its number, e.g. [2]. Use it for what a document says, and the "
    "database tools for stock, sales and purchases."
)


def document_tools(shelves: list[str] | None = None) -> list[Tool]:
    """`shelves` are named in the description so the model can pick one."""
    description = DESCRIPTION
    if shelves:
        description += f" Shelves: {', '.join(shelves)}."
    return [
        Tool(
            name="search_documents",
            description=description,
            args=SearchDocumentsArgs,
            handler=search_documents,
        )
    ]
