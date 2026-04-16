# MemSearch MCP Server

MCP server that gives agents persistent, cross-session memory backed by:

- **Dated markdown files** — the source of truth, human-readable and auditable
- **Milvus-lite** — file-based vector index (no separate server required)
- **Hybrid search** — dense vector + BM25 full-text with RRF reranking

**Why MemSearch over LightMem?**

| | MemSearch MCP | LightMem MCP |
|---|---|---|
| LLM calls at write time | None | Yes (`force_extract`) |
| Embedding options | OpenAI / Ollama / Local / ONNX | OpenAI-compatible only |
| Zero-cost option | ✓ (`local` or `ollama` provider) | ✗ |
| Storage readable | ✓ (plain markdown files) | ✗ (Qdrant vectors) |
| External server required | ✗ (milvus-lite by default) | ✓ (Qdrant) |
| Hybrid BM25 + vector search | ✓ | ✗ (vector only) |

---

## Tools

| Tool | Replaces (LightMem) | Description |
|---|---|---|
| `get_current_date` | `get_timestamp` | Returns current ISO 8601 datetime |
| `write_memory` | `add_memory` | Appends entry to dated `.md` file, auto-indexes |
| `search_memory` | `retrieve_memory` | Hybrid dense+BM25 search with optional date filter |
| `list_memory_files` | *(new)* | Lists dated markdown files in memory directory |
| `reindex_memory` | `offline_update` | Rebuilds Milvus index from all markdown files |
| `show_config` | *(new)* | Shows resolved env var configuration |

---

## Quickstart

### Local development (zero cost — fully offline)

```bash
pip install "memsearch[local]" fastmcp

export MEMSEARCH_MEMORY_PATH="/home/user/.memsearch/memories"
export MEMSEARCH_EMBEDDING_PROVIDER="local"

python server.py
```

### With Ollama (zero cost — requires Ollama running)

```bash
ollama pull nomic-embed-text

pip install "memsearch[ollama]" fastmcp

export MEMSEARCH_MEMORY_PATH="/home/user/.memsearch/memories"
export MEMSEARCH_EMBEDDING_PROVIDER="ollama"
export MEMSEARCH_EMBEDDING_MODEL="nomic-embed-text"

python server.py
```

### With OpenAI-compatible endpoint

```bash
pip install "memsearch" fastmcp

export MEMSEARCH_MEMORY_PATH="/home/user/.memsearch/memories"
export MEMSEARCH_EMBEDDING_PROVIDER="openai"
export MEMSEARCH_EMBEDDING_API_KEY="your-key"
# Optional: route through a self-hosted endpoint
# export MEMSEARCH_EMBEDDING_BASE_URL="https://your-openai-compatible-endpoint/v1"

python server.py
```

---

## Agent Studio Configuration

### MCP Server JSON (paste into Agent Studio → MCP Servers)

```json
{
  "mcpServers": {
    "memsearch": {
      "command": "python",
      "args": ["path/to/memsearch_mcp/server.py"],
      "env": {
        "MEMSEARCH_MEMORY_PATH": "${MEMSEARCH_MEMORY_PATH}",
        "MEMSEARCH_MILVUS_URI": "${MEMSEARCH_MILVUS_URI}",
        "MEMSEARCH_COLLECTION": "${MEMSEARCH_COLLECTION}",
        "MEMSEARCH_EMBEDDING_PROVIDER": "${MEMSEARCH_EMBEDDING_PROVIDER}",
        "MEMSEARCH_EMBEDDING_API_KEY": "${MEMSEARCH_EMBEDDING_API_KEY}"
      }
    }
  }
}
```

### Required Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MEMSEARCH_MEMORY_PATH` | **Yes** | Persistent directory for markdown memory files |
| `MEMSEARCH_MILVUS_URI` | No | Milvus path/URL (default: `{MEMORY_PATH}/milvus.db`) |
| `MEMSEARCH_COLLECTION` | No | Collection name — use unique value per workflow |
| `MEMSEARCH_EMBEDDING_PROVIDER` | No | `openai` (default), `ollama`, `local`, `onnx` |
| `MEMSEARCH_EMBEDDING_API_KEY` | Depends | Required for `openai`, `google`, `voyage` providers |
| `MEMSEARCH_EMBEDDING_BASE_URL` | No | OpenAI-compatible endpoint base URL |
| `MEMSEARCH_EMBEDDING_MODEL` | No | Override default model for the chosen provider |

### Persistent Storage in CML / Agent Studio

Agent Studio MCP servers run in sandboxed environments with filesystem isolation.
`MEMSEARCH_MEMORY_PATH` and `MEMSEARCH_MILVUS_URI` **must** point to a persistent location:

```
# CML Workbench — user home is persistent
MEMSEARCH_MEMORY_PATH=/home/cdsw/.memsearch/memories
MEMSEARCH_MILVUS_URI=/home/cdsw/.memsearch/milvus.db

# Or use a shared project directory
MEMSEARCH_MEMORY_PATH=/home/cdsw/project-name/memory
MEMSEARCH_MILVUS_URI=/home/cdsw/project-name/memory/milvus.db
```

> `/home/cdsw/` is persistent across sessions in CML. Do **not** use session-specific
> temp paths — writes will be lost on pod restart.

---

## Memory File Format

Memory is stored as dated markdown files under `MEMSEARCH_MEMORY_PATH`:

```
~/.memsearch/memories/
  2026-03-25.md      ← today's memories
  2026-03-24.md      ← yesterday's memories
  2026-03-20.md
  milvus.db          ← Milvus-lite vector index
```

Each `write_memory` call appends an H2-headed entry to today's file:

```markdown
## 14:32:10

CUSTOMER: Maria Garcia (CUST-B003).
QUERY TYPE: ACCOUNT_STATUS.
ACCOUNT: ACC-100006 (CHECKING, ****8844). STATUS: LOCKED. LOCK REASON: FRAUD_ALERT.
BALANCE: $1,234.56 total / $0.00 available.
ISSUE: Customer's card declined. Account locked after two unauthorized transactions.
ACTION TAKEN: Explained lock. Transferred to fraud team. Provided case ref CASE-2026-0001.
ESCALATED: Yes — FRAUD_ALERT. Fraud specialist team.

## ACCOUNT_STATUS - Sarah Williams

CUSTOMER: Sarah Williams (CUST-B005).
QUERY TYPE: ACCOUNT_STATUS.
ACCOUNT: ACC-100009 (CHECKING, ****3377). STATUS: FROZEN. LOCK REASON: SUSPICIOUS_ACTIVITY.
...
```

Files are plain text — you can open, read, edit, and delete entries directly.
The Milvus index is rebuilt from files via `reindex_memory` if edits are made outside the MCP.

---

## Agent Usage Patterns

### Pattern 1 — Recall at session start

Call `search_memory` with the user's first message before doing anything else.
This surfaces prior context without the agent needing to know what to ask for.

```
Task 1 (Memory Recall & Intent Detection):
1. Call search_memory(query={input}, top_k=5)
2. Review results — summarise any relevant prior context
3. Classify intent from {input} + prior context
```

### Pattern 2 — Store at session end

After composing the response to the user, store a compact structured note.
Do NOT store the full conversation — store only what a future session needs.

```
Task 3B (Memory Storage):
1. Call get_current_date() → timestamp
2. Call write_memory(
     content="CUSTOMER: ... ACCOUNT: ... ISSUE: ... ACTION: ... ESCALATED: ...",
     heading="ACCOUNT_STATUS - Maria Garcia"
   )
```

### Pattern 3 — Date-scoped recall for recent context

When a user says "last time we spoke" or "the issue from yesterday":

```python
search_memory(
    query="Maria Garcia account locked",
    top_k=3,
    source_date_prefix="2026-03-24"   # yesterday only
)
```

---

## Embedding Provider Quick Reference

| Provider | `EMBEDDING_PROVIDER` | Default model | Cost | Requires |
|---|---|---|---|---|
| OpenAI | `openai` | text-embedding-3-small | ~$0.02/1M tokens | API key |
| OpenAI-compatible | `openai` | (set via model) | varies | API key + base URL |
| Ollama | `ollama` | nomic-embed-text | Free | Ollama server |
| Local sentence-transformers | `local` | all-MiniLM-L6-v2 | Free | ~500 MB download |
| ONNX | `onnx` | gpahal/bge-m3-onnx-int8 | Free | ~250 MB download |
| Google Gemini | `google` | gemini-embedding-001 | paid | Google API key |
| Voyage AI | `voyage` | voyage-3-lite | paid | Voyage API key |

For Agent Studio deployments where the workflow LLM is already on an OpenAI-compatible
endpoint, routing `MEMSEARCH_EMBEDDING_BASE_URL` to the same endpoint (if it supports
embeddings) avoids adding a new external dependency.
