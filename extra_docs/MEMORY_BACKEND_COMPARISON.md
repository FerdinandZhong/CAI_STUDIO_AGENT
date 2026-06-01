# Memory Backend Comparison: MemSearch vs MemPalace for Agent Studio

## Context

This document compares two memory systems available for native integration into Agent Studio:
- **MemSearch** — a markdown-first memory system with hybrid search, already present in `memsearch_mcp/`
- **MemPalace** — a hierarchical memory palace system with knowledge graph, maintained as a separate project

Both can serve as the memory backend described in `NATIVE_MEMORY_MANAGEMENT_DESIGN.md`. The design doc's architecture is backend-agnostic at the data model level (Phase 4 explicitly plans for backend swappability), so the choice affects implementation effort and capabilities, not fundamental architecture.

---

## Architecture Overview

### MemSearch

```
User/Agent → FastMCP Server (6 tools)
                 ↓
          Dated Markdown Files (YYYY-MM-DD.md)
                 ↓
          Milvus-lite Index (single .db file)
              ├── Dense vector embeddings
              └── BM25 full-text index
              └── RRF reranking
```

**Storage model**: Append-only dated markdown files. Each memory is an H2-headed section in a day file. Chunks are SHA-256 deduplicated at index time. Milvus-lite stores the hybrid index as a single file alongside the markdown.

### MemPalace

```
User/Agent → MCP Server (20+ tools)
                 ↓
          ChromaDB (SQLite + HNSW binary segments)
              ├── mempalace_drawers (verbatim content chunks)
              └── mempalace_closets (topic pointer index)
                 ↓
          SQLite Knowledge Graph
              ├── entities (name, type, properties)
              └── triples (subject, predicate, object, valid_from, valid_to)
```

**Storage model**: Hierarchical palace structure — Wings (person/project) > Rooms (day/aspect) > Drawers (verbatim 800-char chunks). A separate closet collection stores compressed topic pointers. A temporal knowledge graph tracks entity relationships with validity windows.

---

## Feature Comparison

| Dimension | MemSearch | MemPalace |
|-----------|-----------|-----------|
| **Storage format** | Dated markdown files | ChromaDB (SQLite + HNSW binary) |
| **Human-readable** | Yes — plain `.md` files, editable | No — binary ChromaDB segments |
| **Search method** | Hybrid BM25 + dense vector + RRF reranking | Hybrid BM25 + vector + closet rank boost |
| **Knowledge graph** | None | Full temporal entity graph (SQLite) |
| **Entity tracking** | None (up to the agent) | Auto-detection, disambiguation, registry |
| **Temporal reasoning** | Date-prefix filtering on file names | `valid_from`/`valid_to` on graph triples |
| **Index technology** | Milvus-lite (single `.db` file) | ChromaDB HNSW (directory of binary segments) |
| **Embedding providers** | OpenAI, Ollama, local, ONNX, Google, Voyage | ChromaDB default (all local, zero API) |
| **Air-gapped support** | Yes (local/Ollama/ONNX) | Yes (all local, zero API keys) |
| **Deduplication** | SHA-256 content hash at index time | Deterministic drawer IDs (hash of source+chunk) |
| **Write-ahead log** | None | JSONL WAL for audit/rollback |
| **MCP tools** | 6 tools | 20+ tools |
| **Complexity** | Low — flat file + index | High — wings/rooms/drawers/closets/tunnels/graph |

---

## MCP Tool Comparison

### MemSearch (6 tools)

| Tool | Purpose |
|------|---------|
| `get_current_date` | Returns ISO 8601 timestamp |
| `write_memory` | Append to today's markdown + auto-index |
| `search_memory` | Hybrid BM25+vector search with optional date filter |
| `list_memory_files` | List dated markdown files (newest first) |
| `reindex_memory` | Rebuild Milvus index from all files |
| `show_config` | Debug: show resolved configuration |

### MemPalace (20+ tools, grouped)

**Read tools**: `mempalace_search`, `mempalace_status`, `mempalace_list_wings`, `mempalace_list_rooms`, `mempalace_get_taxonomy`, `mempalace_check_duplicate`

**Write tools**: `mempalace_add_drawer`, `mempalace_delete_drawer`, `mempalace_update_drawer`, `mempalace_diary_write`, `mempalace_diary_read`

**Graph traversal**: `mempalace_traverse`, `mempalace_find_tunnels`, `mempalace_create_tunnel`, `mempalace_list_tunnels`, `mempalace_delete_tunnel`, `mempalace_follow_tunnels`

**Knowledge graph**: `mempalace_kg_query`, `mempalace_kg_add`, `mempalace_kg_invalidate`, `mempalace_kg_timeline`, `mempalace_kg_stats`

**Maintenance**: `mempalace_reconnect`, `mempalace_hook_settings`

---

## Agent Studio Integration Fit

### Alignment with Design Doc (`NATIVE_MEMORY_MANAGEMENT_DESIGN.md`)

| Design Doc Requirement | MemSearch | MemPalace |
|------------------------|-----------|-----------|
| **Workflow-level collection isolation** | `MEMSEARCH_COLLECTION` env var per workflow | Palace path per workflow (directory-level isolation) |
| **Session-scoped retrieval** | `source_date_prefix` filter (date-level) | `where` clause on wing/room metadata |
| **Auto-inject at session start** | `search_memory(query)` in engine | `mempalace_search(query, wing=...)` in engine |
| **Content gate filtering (Layer 1)** | Engine-side, pre-`write_memory` | Engine-side, pre-`mempalace_add_drawer` |
| **Semantic dedup (Layer 2)** | Engine-side, query-before-write | Built-in `mempalace_check_duplicate` tool |
| **Memory note schema (Layer 3)** | Backstory injection (same for both) | Backstory injection (same for both) |
| **store_mode enum** | `AGENT_CONTROLLED` / `AUTO_WRITE` / `DISABLED` | Same — engine-level concern |
| **Scheduled consolidation** | `reindex_memory` (rebuild index) | `repair` module + dedup module |
| **Backend adapter ABC** | Simple: write + search + list | Complex: write + search + graph + traverse |
| **Export/migration** | Markdown files copy directly | ChromaDB directory copy |

### Integration Effort

| Aspect | MemSearch | MemPalace |
|--------|-----------|-----------|
| **In-repo** | Yes (`memsearch_mcp/`) | No (separate project at `~/Projects/mempalace`) |
| **Design doc status** | Phase 4 planned explicitly | Not mentioned |
| **MCP server ready** | Yes (FastMCP, production-ready) | Yes (MCP server in `mcp_server.py`) |
| **Env vars needed** | 3-4 (`PATH`, `COLLECTION`, `PROVIDER`, optional `API_KEY`) | 1-2 (`PALACE_PATH`, optional config) |
| **Dependency footprint** | `memsearch>=0.1.18`, `fastmcp>=2.0.0`, embedding provider | `mempalace` package, ChromaDB |
| **Tool count for agent** | 2-3 (write, search, date) — low cognitive load | 5-10+ depending on mode — higher cognitive load |
| **Auto-provisioner complexity** | Low — few env vars, few tools to subset | Medium — more tools to subset by memory_mode |

---

## Integration Points in Agent Studio

Both backends plug into the same code paths. The key files and their changes:

### 1. MCP Tool Injection — `studio/workflow_engine/src/engine/crewai/crew.py`

Lines 36-38 assemble per-agent tools from `tool_instance_ids` + `mcp_instance_ids`. The memory provisioner would add the memory MCP instance here based on `agent.memory_mode`:

```python
# Current code (crew.py:36-38):
crewai_tools = [tools[tool_id] for tool_id in agent.tool_instance_ids]
for mcp_id in agent.mcp_instance_ids:
    crewai_tools.extend(mcps[mcp_id].tools)

# With memory integration:
# memory MCP instance is auto-provisioned and its tools filtered by memory_mode
```

### 2. Backstory Injection — `studio/workflow_engine/src/engine/crewai/agents.py`

Line 19 passes `backstory` directly to CrewAI. Memory storage instructions would be appended for WRITE_ONLY/READ_WRITE agents.

### 3. MCP Spawning — `studio/workflow_engine/src/engine/crewai/mcp.py`

`get_mcp_tools_for_crewai()` (line 54) spawns MCP servers via `uvx`/`npx` with env vars. Both MemSearch and MemPalace would be spawned identically — they're both Python MCP servers.

### 4. New Module — `studio/as_mcp/memory_provisioner.py`

Auto-creates/destroys the memory MCP instance when `WorkflowMemoryConfig.enabled` is toggled. Tool subsetting logic differs:

**MemSearch tool subsetting:**
- `READ_ONLY` → `search_memory`, `list_memory_files`
- `WRITE_ONLY` → `write_memory`, `get_current_date`
- `READ_WRITE` → all tools

**MemPalace tool subsetting:**
- `READ_ONLY` → `mempalace_search`, `mempalace_list_wings`, `mempalace_list_rooms`, `mempalace_kg_query`
- `WRITE_ONLY` → `mempalace_add_drawer`, `mempalace_kg_add`
- `READ_WRITE` → all read + write tools (potentially 10-15 tools)

---

## Strengths & Weaknesses Summary

### MemSearch

**Strengths:**
- Already in the Agent Studio repo — zero setup friction
- Explicitly planned in the design doc (Phase 4)
- Simple tool surface (6 tools) — low cognitive load for agents
- Human-readable markdown storage — easy auditing and debugging
- Multiple embedding providers including zero-cost local options
- Flat storage model maps cleanly to workflow isolation (`COLLECTION` per workflow)

**Weaknesses:**
- No knowledge graph — cannot track entity relationships or temporal facts
- No entity detection/disambiguation — agents must structure their own notes
- No dedup tool — dedup must be implemented engine-side (Layer 2)
- Date-level scoping only — no wing/room granularity for organizing by topic
- No write-ahead log — no built-in audit trail for memory poisoning detection

### MemPalace

**Strengths:**
- Temporal knowledge graph — tracks entity relationships with validity windows
- Auto entity detection and disambiguation across content
- Hierarchical organization (wings/rooms) — natural topic separation
- Write-ahead log for audit/rollback
- Built-in dedup checking (`mempalace_check_duplicate`)
- Closet index layer provides additional search signal beyond raw vector similarity
- AAAK compression for efficient LLM scanning of large memory sets

**Weaknesses:**
- Separate project — not in the Agent Studio repo, not in the design doc
- Complex tool surface (20+ tools) — higher cognitive load for agents
- ChromaDB storage is not human-readable (binary HNSW segments)
- More complex provisioner needed (more tools to subset, more env vars)
- Heavier dependency (ChromaDB + SQLite KG)
- Wing/room routing adds conceptual overhead for simple use cases

---

## Recommendation

### For MVP (Phase 1-2): MemSearch

MemSearch is the pragmatic first choice:
- Already in-repo, already planned in the design doc
- Minimal tool surface keeps agent behavior predictable
- Markdown storage makes debugging straightforward during early integration
- Workflow isolation via `MEMSEARCH_COLLECTION` maps directly to the design doc's `collection_name`

### For Advanced Use Cases (Phase 4+): MemPalace as Optional Backend

MemPalace becomes valuable when workflows need:
- Cross-entity relationship tracking ("Who worked with whom on what?")
- Temporal fact management ("What was the account status as of March 15?")
- Multi-wing organization (separating memories by person/project within a workflow)
- Audit trails via write-ahead logging

The design doc's `MemoryBackend` ABC (Phase 3) should accommodate both:

```python
class MemoryBackend(ABC):
    async def write(self, content: str, metadata: dict) -> str: ...
    async def search(self, query: str, top_k: int, filters: dict) -> list: ...
    async def list_entries(self) -> list: ...
    async def delete(self, entry_id: str) -> bool: ...

class MemSearchBackend(MemoryBackend): ...   # Phase 4
class MemPalaceBackend(MemoryBackend): ...   # Phase 4+
class LightMemBackend(MemoryBackend): ...    # Phase 1 (current recommendation)
```

### Hybrid Option: MemSearch Storage + MemPalace Knowledge Graph

A future integration could use MemSearch for content storage (markdown files, simple read/write) while using MemPalace's knowledge graph module independently for entity-relationship tracking. The `knowledge_graph.py` module is self-contained SQLite and could be imported without the full palace infrastructure.

---

## Appendix: Environment Variable Reference

### MemSearch

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MEMSEARCH_MEMORY_PATH` | No | `~/.memsearch/memories` | Directory for markdown files |
| `MEMSEARCH_MILVUS_URI` | No | `{MEMORY_PATH}/milvus.db` | Milvus database path or remote URI |
| `MEMSEARCH_COLLECTION` | No | `memsearch_memory` | Collection name (use unique per workflow) |
| `MEMSEARCH_EMBEDDING_PROVIDER` | No | `openai` | `openai`, `ollama`, `local`, `onnx`, `google`, `voyage` |
| `MEMSEARCH_EMBEDDING_MODEL` | No | Provider default | Override embedding model |
| `MEMSEARCH_EMBEDDING_API_KEY` | Depends | Falls back to `OPENAI_API_KEY` | API key for external providers |
| `MEMSEARCH_EMBEDDING_BASE_URL` | No | Provider default | Custom endpoint URL |
| `MEMSEARCH_MAX_CHUNK_SIZE` | No | `1500` | Max characters per chunk |
| `MEMSEARCH_OVERLAP_LINES` | No | `2` | Overlap lines between chunks |

### MemPalace

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MEMPALACE_PALACE_PATH` | No | `~/.mempalace/palace` | ChromaDB storage directory |
| `MEMPALACE_ENTITY_LANGUAGES` | No | `en` | Entity detection languages |
