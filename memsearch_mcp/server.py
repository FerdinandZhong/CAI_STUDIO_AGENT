#!/usr/bin/env python3
"""
MemSearch MCP Server

Exposes memory read/write tools backed by:
  - Markdown files (human-readable, version-controllable source of truth)
  - Milvus-lite for hybrid dense-vector + BM25 search (no separate server required)

Advantages over LightMem:
  - Zero LLM API calls — only an embedding model is needed
  - Local/Ollama embedding providers supported (fully air-gapped, zero cost)
  - Memory files are plain markdown — readable, editable, auditable
  - Milvus-lite stores the index in a single .db file alongside the markdown files

Environment Variables
---------------------
MEMSEARCH_MEMORY_PATH
    Directory for dated markdown memory files.
    Must be a persistent location (e.g. /home/cdsw/.memsearch/memories).
    Default: ~/.memsearch/memories

MEMSEARCH_MILVUS_URI
    Path or URI for the Milvus database.
    - Local file (milvus-lite):  /home/cdsw/.memsearch/milvus.db  (default)
    - Remote Milvus server:      http://milvus-host:19530
    If unset, defaults to {MEMSEARCH_MEMORY_PATH}/milvus.db

MEMSEARCH_COLLECTION
    Milvus collection name. Use a unique name per workflow to isolate memories.
    Default: memsearch_memory

MEMSEARCH_EMBEDDING_PROVIDER
    Embedding backend. Options:
      openai   — OpenAI text-embedding-3-small (requires API key, cheapest external option)
      ollama   — Local Ollama server (zero cost, requires Ollama running)
      local    — sentence-transformers all-MiniLM-L6-v2 (zero cost, fully offline)
      onnx     — ONNX runtime BGE model (zero cost, lighter than sentence-transformers)
      google   — Google Gemini embeddings
      voyage   — Voyage AI embeddings
    Default: openai

MEMSEARCH_EMBEDDING_MODEL
    Override the default model for the chosen provider. Optional.

MEMSEARCH_EMBEDDING_API_KEY
    API key for the embedding provider (openai, google, voyage).
    Falls back to OPENAI_API_KEY if unset.

MEMSEARCH_EMBEDDING_BASE_URL
    Base URL for OpenAI-compatible embedding endpoints.
    Useful for routing through a self-hosted or proxied endpoint.

MEMSEARCH_MAX_CHUNK_SIZE
    Maximum characters per indexed chunk. Default: 1500.

MEMSEARCH_OVERLAP_LINES
    Number of lines of overlap between adjacent chunks. Default: 2.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MEMORY_PATH = Path(
    os.environ.get("MEMSEARCH_MEMORY_PATH", str(Path.home() / ".memsearch" / "memories"))
)

_MILVUS_URI = os.environ.get(
    "MEMSEARCH_MILVUS_URI", str(_MEMORY_PATH / "milvus.db")
)

_COLLECTION = os.environ.get("MEMSEARCH_COLLECTION", "memsearch_memory")

_EMBEDDING_PROVIDER = os.environ.get("MEMSEARCH_EMBEDDING_PROVIDER", "openai")
_EMBEDDING_MODEL = os.environ.get("MEMSEARCH_EMBEDDING_MODEL") or None
_EMBEDDING_API_KEY = (
    os.environ.get("MEMSEARCH_EMBEDDING_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or None
)
_EMBEDDING_BASE_URL = os.environ.get("MEMSEARCH_EMBEDDING_BASE_URL") or None
_MAX_CHUNK_SIZE = int(os.environ.get("MEMSEARCH_MAX_CHUNK_SIZE", "1500"))
_OVERLAP_LINES = int(os.environ.get("MEMSEARCH_OVERLAP_LINES", "2"))

# ---------------------------------------------------------------------------
# Singleton MemSearch instance
# ---------------------------------------------------------------------------

_mem_instance = None


def _get_mem():
    """
    Lazy-initialise the MemSearch instance.
    The instance is created once and reused across all tool calls.
    """
    global _mem_instance
    if _mem_instance is None:
        from memsearch import MemSearch

        _MEMORY_PATH.mkdir(parents=True, exist_ok=True)

        _mem_instance = MemSearch(
            paths=[str(_MEMORY_PATH)],
            embedding_provider=_EMBEDDING_PROVIDER,
            embedding_model=_EMBEDDING_MODEL,
            embedding_api_key=_EMBEDDING_API_KEY,
            embedding_base_url=_EMBEDDING_BASE_URL,
            milvus_uri=_MILVUS_URI,
            collection=_COLLECTION,
            max_chunk_size=_MAX_CHUNK_SIZE,
            overlap_lines=_OVERLAP_LINES,
        )
    return _mem_instance


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "memsearch-memory",
    instructions=(
        "Memory tools backed by dated markdown files and hybrid vector+BM25 search. "
        "Use write_memory to store structured notes and search_memory to recall them "
        "across sessions. Prefer compact, structured content over verbatim conversation text."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1: get_current_date
# ---------------------------------------------------------------------------

@mcp.tool()
def get_current_date() -> str:
    """
    Return the current date and time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS).

    Call this before write_memory when you need a precise timestamp for the heading.
    """
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Tool 2: write_memory
# ---------------------------------------------------------------------------

@mcp.tool()
async def write_memory(content: str, heading: str | None = None) -> dict[str, Any]:
    """
    Write a memory entry to today's dated markdown file and index it immediately.

    The entry is appended to {MEMORY_PATH}/YYYY-MM-DD.md under an H2 heading.
    Milvus is updated in-place — no separate indexing step needed.

    Storage format
    --------------
    File: YYYY-MM-DD.md
    Entry:
        ## <heading>

        <content>

    Retrieval
    ---------
    The heading and content are both indexed. The heading is preserved in search
    results so the agent can identify which session a memory came from.

    Best practices
    --------------
    - Store a STRUCTURED NOTE, not raw conversation text.
      Dense, identifier-rich notes make retrieval far more precise.
    - Include key entities: customer IDs, account numbers, transaction IDs, case refs.
    - One write_memory call per significant interaction turn, not per message.
    - Use a descriptive heading (e.g. "ACCOUNT_STATUS - Maria Garcia") rather than
      leaving it blank, to make list_memory_files and manual review easier.

    Args:
        content: The memory content. Should be a compact structured note.
        heading: Optional H2 heading. Defaults to the current HH:MM:SS timestamp.

    Returns:
        dict with:
          file          — absolute path of the file written
          heading       — the heading used
          chunks_indexed — number of chunks indexed from this file after the write
    """
    mem = _get_mem()
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    heading_str = heading or now.strftime("%H:%M:%S")

    file_path = _MEMORY_PATH / f"{date_str}.md"

    # Append the new entry. Two blank lines before the heading ensure it is
    # parsed as a separate chunk from the preceding entry.
    entry_text = f"\n\n## {heading_str}\n\n{content.strip()}\n"
    with file_path.open("a", encoding="utf-8") as fh:
        fh.write(entry_text)

    chunks = await mem.index_file(file_path)

    return {
        "file": str(file_path),
        "heading": heading_str,
        "chunks_indexed": chunks,
    }


# ---------------------------------------------------------------------------
# Tool 3: search_memory
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_memory(
    query: str,
    top_k: int = 5,
    source_date_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search memory files for content semantically similar to the query.

    Uses hybrid search: dense vector similarity + BM25 full-text, merged with
    Reciprocal Rank Fusion (RRF) reranking. This returns more precise results
    than pure vector search, especially for queries containing specific identifiers
    (account numbers, transaction IDs, customer names).

    Session start pattern
    ---------------------
    At the start of each conversation, call search_memory with the user's first
    message as the query to recall relevant prior context before responding.

    Args:
        query: Natural language description of what to recall.
               For best results, include any identifiers from the user's message
               (e.g. "Maria Garcia account locked fraud" not just "account issue").
        top_k: Maximum number of results to return (default 5).
        source_date_prefix: Optional date filter. Restrict results to files whose
               names start with this prefix.
               Examples:
                 "2026-03-25"  → only today's memories
                 "2026-03"     → all of March 2026
                 "2026"        → all of this year
               If omitted, searches across all memory files.

    Returns:
        List of result dicts, ordered by relevance score (highest first):
          content  — the memory text chunk
          source   — absolute path of the source markdown file
          heading  — the H2 heading under which the chunk appears
          score    — RRF relevance score (higher is better, not bounded to [0,1])
    """
    mem = _get_mem()

    # Fetch extra results to allow for source filtering without losing top-k.
    fetch_k = top_k * 3 if source_date_prefix else top_k
    results = await mem.search(query, top_k=fetch_k)

    if source_date_prefix:
        results = [
            r for r in results
            if Path(r.get("source", "")).stem.startswith(source_date_prefix)
        ]

    results = results[:top_k]

    return [
        {
            "content": r.get("content", ""),
            "source": str(r.get("source", "")),
            "heading": r.get("heading", ""),
            "score": round(float(r.get("score", 0.0)), 4),
        }
        for r in results
    ]


# ---------------------------------------------------------------------------
# Tool 4: list_memory_files
# ---------------------------------------------------------------------------

@mcp.tool()
def list_memory_files() -> list[dict[str, str]]:
    """
    List all markdown memory files in the memory directory, newest first.

    Useful for:
    - Auditing what memories have been stored
    - Identifying the date range of available memory
    - Debugging (e.g. confirming a write_memory call created today's file)

    Returns:
        List of dicts, each with:
          file       — filename (e.g. "2026-03-25.md")
          date       — date extracted from filename (e.g. "2026-03-25")
          size_bytes — file size as a string
    """
    if not _MEMORY_PATH.exists():
        return []

    files = sorted(_MEMORY_PATH.glob("*.md"), key=lambda f: f.stem, reverse=True)
    return [
        {
            "file": f.name,
            "date": f.stem,
            "size_bytes": str(f.stat().st_size),
        }
        for f in files
    ]


# ---------------------------------------------------------------------------
# Tool 5: reindex_memory
# ---------------------------------------------------------------------------

@mcp.tool()
async def reindex_memory() -> dict[str, int]:
    """
    Re-scan all markdown files in the memory directory and rebuild the Milvus index.

    When to use
    -----------
    - After manually editing or deleting a memory file outside of write_memory
    - To repair an index that has drifted out of sync with the files
    - After restoring memory files from a backup

    Not needed after write_memory — that tool auto-indexes the updated file.

    Returns:
        dict with:
          total_chunks — number of chunks now indexed across all files
    """
    mem = _get_mem()
    count = await mem.index()
    return {"total_chunks": count}


# ---------------------------------------------------------------------------
# Tool 6: show_config (debug)
# ---------------------------------------------------------------------------

@mcp.tool()
def show_config() -> dict[str, str]:
    """
    Show the active MemSearch MCP server configuration.

    Returns the resolved values of all environment variables. Useful for
    verifying the server is pointing at the correct memory path and embedding
    provider before running workflows.

    API keys are masked for security.
    """
    api_key_display = (
        f"{_EMBEDDING_API_KEY[:6]}...{_EMBEDDING_API_KEY[-4:]}"
        if _EMBEDDING_API_KEY and len(_EMBEDDING_API_KEY) > 10
        else ("(not set)" if not _EMBEDDING_API_KEY else "(set)")
    )
    return {
        "memory_path": str(_MEMORY_PATH),
        "milvus_uri": _MILVUS_URI,
        "collection": _COLLECTION,
        "embedding_provider": _EMBEDDING_PROVIDER,
        "embedding_model": _EMBEDDING_MODEL or "(provider default)",
        "embedding_api_key": api_key_display,
        "embedding_base_url": _EMBEDDING_BASE_URL or "(provider default)",
        "max_chunk_size": str(_MAX_CHUNK_SIZE),
        "overlap_lines": str(_OVERLAP_LINES),
        "memory_path_exists": str(_MEMORY_PATH.exists()),
        "milvus_db_exists": str(Path(_MILVUS_URI).exists() if not _MILVUS_URI.startswith("http") else "remote"),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()
