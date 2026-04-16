# Native Memory Management — Agent Studio Design Plan

## Context

The current LightMem integration works as a manually-configured MCP server: users add the MCP
template, create an instance per workflow, assign individual tools to individual agents, and
manage the collection name and backend config themselves. This works but puts too much
configuration burden on the user and gives the system no visibility into memory as a
first-class concern.

This document proposes integrating memory management natively into Agent Studio — as a
workflow-level configuration object backed by either **LightMem** or **MemSearch**, with
engine-level lifecycle management rather than pure agent prompt orchestration.

---

## 1. Backend Decision: LightMem vs MemSearch

### LightMem
**Storage model:** `(user_input, assistant_reply)` pairs embedded and stored in Qdrant
(remote or local). Entity extraction and segmentation run at write time.

**Retrieval:** Semantic vector search against Qdrant with optional `filters` dict for
session/user scoping.

**Key tools:** `add_memory`, `retrieve_memory`, `offline_update`, `get_timestamp`,
`configure_lightmem`.

**Agent Studio fit:**
- Already proven in the bubblewrap sandbox (confirmed in the invoice parser demo)
- Remote Qdrant mode works around filesystem isolation
- Qdrant is already deployable as a CAI Application (SP_hol/qdrant_cai_app)
- `filters` supports per-session memory scoping without separate collections
- `offline_update` provides periodic memory consolidation

**Gaps:**
- Requires OpenAI API key for embeddings (or OpenAI-compatible endpoint)
- Memory is opaque in the vector store — not directly human-readable/editable
- No MCP server for MemSearch-style hybrid BM25+vector search

### MemSearch
**Storage model:** Markdown files on disk (e.g. `2026-03-24.md`), chunked by heading and
paragraph, deduplicated via SHA-256, indexed into Milvus with hybrid dense+BM25 search and
RRF reranking.

**Retrieval:** Hybrid dense+BM25 search with `source_prefix` scoping; CLI, Python API, and
framework integrations (CrewAI, LangChain, LlamaIndex).

**Agent Studio fit (gaps):**
- **No MCP server** — would require building one from scratch
- Requires Milvus (another infrastructure dependency vs Qdrant already deployed)
- File-based storage hits the same bubblewrap filesystem isolation problem as LightMem local
  mode — needs a mounted persistent volume or remote Milvus
- Not yet proven in Agent Studio environments

**Unique strengths:**
- Markdown source-of-truth: memories are human-readable, editable, and auditable
- Content-hash deduplication prevents redundant re-embedding at index time
- File watcher enables real-time memory updates without explicit API calls

### Recommendation: **LightMem — with MemSearch's auditability principle borrowed**

LightMem is the pragmatic choice for the first native integration:
- Infrastructure already available and sandbox-proven
- `filters` + `collection_name` give us both workflow-isolation and session-scoping
- `offline_update` maps cleanly to a scheduled CML Job

The core insight from MemSearch worth adopting: **structured memory notes over verbatim
storage**. Rather than storing raw agent responses, agents are prompted (or the engine
enforces) storing compact structured summaries. This gives us the auditability benefit
without requiring a markdown filesystem.

MemSearch remains an attractive future option once it gains an MCP server. The design below
is backend-agnostic at the data model level to keep that migration path open.

---

## 2. Memory Granularity: Workflow-level vs Agent-level

### Discussion

The current MCP approach is agent-level by default (each agent independently decides which
MCP tools to call). This works for small workflows but creates problems:

- **No shared namespace**: two agents in the same workflow could write to different
  collections unless the user carefully configures both
- **No read-before-write coordination**: Agent A stores a fact; Agent B doesn't know to
  retrieve it
- **Redundant retrieval**: every agent independently queries memory, generating redundant
  embedding lookups

### Recommendation: **Workflow-level config with per-agent participation flags**

```
Workflow
  └── MemoryConfig (one per workflow)
        ├── enabled: bool
        ├── backend_type: "lightmem" | "memsearch"
        ├── collection_name: auto-derived (workflow_{id}) or overridden
        ├── auto_inject: bool  ← engine retrieves and injects at session start
        ├── filter_config: dedup_threshold, min_length, store_mode
        └── backend_config: qdrant_url, api_key, embedding_model, ...

Agent (per-agent participation)
  └── memory_mode: NONE | READ_ONLY | WRITE_ONLY | READ_WRITE
```

**How it works:**
- The `MemoryConfig` is a single source of truth for the entire workflow — one collection,
  one backend, one set of filter rules
- Each agent has a `memory_mode` flag controlling which tools are auto-provisioned to it:
  - `NONE`: no memory tools (default, backwards-compatible)
  - `READ_ONLY`: `retrieve_memory` only — information consumers (query agents, Q&A agents)
  - `WRITE_ONLY`: `add_memory` + `get_timestamp` only — information producers (OCR agents,
    extraction agents)
  - `READ_WRITE`: all memory tools — general-purpose agents, manager agents
- The MCP instance backing the memory config is **auto-created and auto-assigned** by the
  engine when memory is enabled — the user does not manually create it

**Rationale for workflow-level:**
- Memory is a property of a workflow's domain, not a single agent's capability (a banking
  chatbot's memory belongs to the workflow, not to any one agent within it)
- Agents within the same workflow should share a memory namespace by default
- Simpler UX: one toggle enables memory for the whole workflow; individual agents opt in/out
- Consistent `collection_name` prevents the current manual-config error where two agents
  write to different collections

---

## 3. Memory Filtering Before Storage

Unfiltered memory storage degrades retrieval quality over time: redundant facts increase
noise, conversational filler pollutes search results, and verbose agent responses waste
embedding budget. Three filtering layers are proposed:

### Layer 1: Content Gate (fast, synchronous, engine-side)
Applied before any embedding call. Rejects content that should never enter memory.

| Rule | Default | Rationale |
|---|---|---|
| `min_content_length` | 30 chars | Filters out single-word acks ("ok", "done", "understood") |
| `max_content_length` | 2000 chars | Truncates verbose responses before embedding; agents should be prompted to store notes, not full replies |
| Pattern blocklist | Greetings, error stack traces, tool debug output | Prevents noise from non-informational exchanges |

### Layer 2: Semantic Deduplication (async, pre-write)
Before calling `add_memory`, the engine calls `retrieve_memory` with the candidate content
and a `limit=1`. If the top result has cosine similarity ≥ `dedup_threshold` (default: 0.92),
the write is skipped (or the existing memory is updated in place if the content is materially
different). This prevents the same fact being stored 10 times across a long session.

```
[Agent produces output]
       ↓
[Content Gate: length + pattern check]
       ↓ (passes)
[retrieve_memory(candidate, limit=1)]
       ↓
similarity ≥ 0.92? → skip (or merge if content changed)
similarity < 0.92? → add_memory(candidate)
```

**Cost note:** The dedup check adds one embedding + one vector query per write. For
high-frequency workflows, this can be made optional (default off for WRITE_ONLY agents in
high-throughput pipelines).

### Layer 3: Memory Note Enforcement (prompt-level)
Rather than storing raw agent output, agents with `memory_mode = WRITE_ONLY | READ_WRITE`
are automatically given a memory note template in their backstory. The engine injects a
standard section into the agent's backstory at runtime:

```
--- MEMORY STORAGE INSTRUCTIONS (auto-injected) ---
When storing information to memory, always call add_memory with a
STRUCTURED NOTE as assistant_reply — not your full conversational response.
The note must follow the workflow's memory schema. Raw conversation text
must NOT be stored verbatim.
```

The memory schema is configurable per workflow in `MemoryConfig.note_schema`. If unset, the
engine provides a generic key-value template. This ensures retrievals return dense,
parseable facts rather than paragraph-length agent replies.

### `store_mode` enum

| Mode | Behaviour |
|---|---|
| `AGENT_CONTROLLED` | Agent calls `add_memory` explicitly (current approach). Filters in Layer 1 and 2 still apply at engine level. |
| `AUTO_WRITE` | Engine automatically stores agent outputs after Layer 1+2 filtering. Agent is not given `add_memory` tool. Best for simple extraction pipelines. |
| `DISABLED` | No writes. Agent may still read. |

---

## 4. Optimisation Strategies

### 4.1 Auto-derived Collection Name
**Current pain point:** User manually sets `LIGHTMEM_COLLECTION_NAME` env var. Wrong value =
memories from different workflows mixed together.

**Native approach:** `collection_name` is auto-derived as a hash of `workflow_id`:
```python
collection_name = f"ws_{hashlib.sha256(workflow_id.encode()).hexdigest()[:12]}"
```
Exposed as read-only in the UI with an optional override for advanced users. Eliminates the
most common misconfiguration.

### 4.2 Session-scoped Retrieval via `filters`
LightMem's `retrieve_memory` accepts a `filters` dict. For conversational workflows, the
engine injects `{"session_id": current_session_id}` automatically at retrieval time. This
ensures a returning user in session B doesn't accidentally surface session A's in-progress
state, while still allowing the agent to query cross-session memories by omitting the filter.

Two retrieval modes exposed in `MemoryConfig`:
- `CROSS_SESSION` (default): no session filter, retrieves from full workflow memory
- `SESSION_SCOPED`: adds session_id filter, retrieves only from current session

### 4.3 Auto-inject at Session Start
When `auto_inject = true`, the workflow engine performs a single memory retrieval at session
initialisation (before the first agent turn) using the user's first message as the query.
Retrieved memories are prepended to the first task's context as a structured block:

```
[Recalled from memory]
{retrieved_memory_1}
{retrieved_memory_2}
---
[User message]
{user_input}
```

This means agents that only need to *read* memory don't need to make explicit `retrieve_memory`
calls — they receive relevant context automatically. Reduces tool call overhead and prevents
the common failure mode where an agent "forgets" to retrieve memory before responding.

### 4.4 Scheduled Memory Consolidation via CML Jobs
`offline_update` (LightMem) merges overlapping memory entries and removes stale duplicates.
This should not run inline (it can be slow on large collections) but as a scheduled
background job.

**Native integration:** When memory is enabled on a workflow, the Studio can optionally
create a CML Job pointing to a bundled `memory_maintenance.py` script:
```python
# Auto-generated by Studio on workflow deploy
from lightmem.mcp import run_offline_update
run_offline_update(collection_name=COLLECTION_NAME, top_k=50, keep_top_n=20)
```
The job runs nightly (configurable). The Studio UI shows "Last consolidated: 2026-03-23
02:00" in the memory settings panel.

### 4.5 Memory Config as a Workflow Export Artefact
When a workflow is exported (packaged for deployment), the `MemoryConfig` is included in the
export bundle — but `backend_config` secrets (API keys, Qdrant URL) are replaced with
`${ENV_VAR}` placeholders. The deploying user fills in environment variables on the target
deployment, ensuring the same memory backend logic works across dev/staging/prod.

### 4.6 Memory Visibility in the Studio UI
Currently memory is invisible to the user — it's just another MCP tool. Native integration
should expose:
- **Memory panel** in workflow settings: enabled/disabled toggle, backend type, collection
  name, last updated, estimated entry count
- **Per-agent memory badge**: visual indicator on agent cards showing their `memory_mode`
  (read, write, read/write, none)
- **Memory inspector** (optional, post-MVP): list/search/delete individual memory entries
  directly from the Studio UI via a new `ListMemoryEntries` / `DeleteMemoryEntry` API

---

## 5. Proposed Implementation — Changes to Agent Studio

### 5.1 Data Model (new DB table + Alembic migration)

```python
# studio/db/model.py — new model
class WorkflowMemoryConfig(Base):
    __tablename__ = "workflow_memory_config"
    id: Mapped[str]           # UUID primary key
    workflow_id: Mapped[str]  # FK -> Workflow.id (unique — one config per workflow)
    enabled: Mapped[bool]     # default False
    backend_type: Mapped[str] # "lightmem" (default) | "memsearch"
    collection_name: Mapped[str]   # auto-derived or user override
    auto_inject: Mapped[bool]      # default True when enabled
    session_scope: Mapped[str]     # "CROSS_SESSION" | "SESSION_SCOPED"
    store_mode: Mapped[str]        # "AGENT_CONTROLLED" | "AUTO_WRITE" | "DISABLED"
    filter_config: Mapped[dict]    # JSON: dedup_threshold, min_length, max_length
    backend_config: Mapped[dict]   # JSON: qdrant_url, api_key, embedding_model, ...
    note_schema: Mapped[str]       # optional memory note template injected into backstory
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

```python
# studio/db/model.py — Agent model extension
class Agent(Base):
    # ... existing fields ...
    memory_mode: Mapped[str]   # "NONE" | "READ_ONLY" | "WRITE_ONLY" | "READ_WRITE"
                               # default "NONE" (backwards-compatible)
```

New Alembic migration:
`alembic/versions/YYYY_MM_DD_add_native_memory_management.py`

### 5.2 Proto Extensions

```protobuf
// New messages in agent_studio.proto

message MemoryFilterConfig {
  float  dedup_threshold    = 1;  // default 0.92
  int32  min_content_length = 2;  // default 30
  int32  max_content_length = 3;  // default 2000
  string store_mode         = 4;  // AGENT_CONTROLLED | AUTO_WRITE | DISABLED
}

message WorkflowMemoryConfig {
  string           workflow_id     = 1;
  bool             enabled         = 2;
  string           backend_type    = 3;  // lightmem | memsearch
  string           collection_name = 4;
  bool             auto_inject     = 5;
  string           session_scope   = 6;  // CROSS_SESSION | SESSION_SCOPED
  MemoryFilterConfig filter_config = 7;
  map<string,string> backend_config = 8;
  string           note_schema     = 9;
}

// New RPC methods in AgentStudio service
rpc GetWorkflowMemoryConfig  (GetWorkflowMemoryConfigRequest)  returns (WorkflowMemoryConfig);
rpc UpdateWorkflowMemoryConfig(UpdateWorkflowMemoryConfigRequest) returns (WorkflowMemoryConfig);
rpc ClearWorkflowMemory      (ClearWorkflowMemoryRequest)      returns (OperationResponse);
```

### 5.3 MCP Auto-provisioning

When `WorkflowMemoryConfig.enabled` is set to `true`, the backend automatically:

1. Creates an `MCPInstance` for the workflow using the bundled LightMem MCP template
2. Sets `env_names` from `backend_config` (QDRANT_URL, OPENAI_API_KEY, LIGHTMEM_COLLECTION_NAME)
3. Sets `activated_tools` based on which agents need which modes:
   - Any agent with `READ_ONLY`: include `retrieve_memory`
   - Any agent with `WRITE_ONLY`: include `add_memory`, `get_timestamp`
   - Any agent with `READ_WRITE`: include all tools
4. Assigns the MCP instance to those agents' `mcp_instance_ids`

When memory is disabled, these auto-created MCP instances are removed. User-created MCP
instances (created manually before this feature) are never touched.

**Responsible module:** `studio/as_mcp/memory_provisioner.py` (new file)

### 5.4 Workflow Engine Changes

```python
# studio/workflow_engine/src/engine/crewai/crew.py

class CrewAIWorkflowRunner:

    async def _initialize_memory_context(self, user_input: str, session_id: str) -> str:
        """Called at session start if auto_inject is enabled."""
        mem_config = self.workflow.memory_config
        if not mem_config or not mem_config.enabled or not mem_config.auto_inject:
            return ""
        filters = {"session_id": session_id} if mem_config.session_scope == "SESSION_SCOPED" else {}
        retrieved = await self.lightmem_client.retrieve_memory(
            query=user_input, limit=5, filters=filters
        )
        if not retrieved:
            return ""
        return "\n".join(f"[Memory] {m}" for m in retrieved)

    async def _should_store_memory(self, content: str, session_id: str) -> bool:
        """Layer 1 + Layer 2 filtering."""
        cfg = self.workflow.memory_config.filter_config
        if len(content) < cfg.min_content_length:
            return False
        if self._matches_blocklist(content):
            return False
        content_truncated = content[:cfg.max_content_length]
        # Layer 2: semantic dedup
        if cfg.dedup_threshold > 0:
            top = await self.lightmem_client.retrieve_memory(query=content_truncated, limit=1)
            if top and top[0].score >= cfg.dedup_threshold:
                return False
        return True
```

Memory context from `_initialize_memory_context` is injected into the first task's
description as a prepended block, then passed through CrewAI's existing `context` mechanism.

### 5.5 Agent Backstory Injection

When assembling agents for execution (`studio/workflow_engine/src/engine/crewai/agents.py`),
if an agent's `memory_mode` is `WRITE_ONLY` or `READ_WRITE`, append the memory note template
to their backstory:

```python
def _build_backstory(agent_db: Agent, memory_config: WorkflowMemoryConfig | None) -> str:
    backstory = agent_db.crew_ai_backstory
    if (memory_config and memory_config.enabled and memory_config.note_schema
            and agent_db.memory_mode in ("WRITE_ONLY", "READ_WRITE")):
        backstory += f"\n\n--- MEMORY STORAGE INSTRUCTIONS ---\n{memory_config.note_schema}"
    return backstory
```

---

## 6. Phased Implementation Plan

### Phase 1 — Foundation (MVP) `feature/native_mem_management`
Scope: data model, MCP auto-provisioning, workflow-level UI toggle

- [ ] Alembic migration: `WorkflowMemoryConfig` table + `Agent.memory_mode` column
- [ ] Proto: `WorkflowMemoryConfig` message + CRUD RPCs
- [ ] `studio/workflow/workflow_memory.py`: `get_memory_config`, `update_memory_config`,
      `clear_memory`
- [ ] `studio/as_mcp/memory_provisioner.py`: auto-create/destroy LightMem MCP instance on
      enable/disable
- [ ] Bundled LightMem MCP template added to `studio-data/mcp_templates/lightmem/`
- [ ] Agent model: `memory_mode` field, per-agent tool auto-assignment
- [ ] UI: memory panel in workflow settings (enable/disable, backend config, collection name)
- [ ] UI: memory mode selector on agent card

### Phase 2 — Engine Integration
Scope: auto_inject, filtering, backstory injection

- [ ] `crew.py`: `_initialize_memory_context()` — auto-inject retrieved memories at session
      start
- [ ] `crew.py`: `_should_store_memory()` — Layer 1 + Layer 2 filtering for `AUTO_WRITE`
      mode
- [ ] `agents.py`: backstory injection for memory note schema
- [ ] Session-scoped retrieval using `filters={"session_id": ...}` when enabled

### Phase 3 — Lifecycle & Observability
Scope: scheduled maintenance job, memory inspector UI, MemSearch adapter

- [ ] CML Job auto-creation for `offline_update` when memory is deployed
- [ ] `studio/workflow/workflow_memory.py`: `list_memory_entries`, `delete_memory_entry`
      (via LightMem HTTP or admin API)
- [ ] UI: memory inspector panel (list recent entries, delete, search)
- [ ] Backend adapter interface: `MemoryBackend` ABC with `LightMemBackend` and
      `MemSearchBackend` implementations, enabling Phase 4 switch

### Phase 4 — MemSearch Option (future, pending MCP server availability)
- [ ] MemSearch MCP server (if published upstream, or build a thin wrapper)
- [ ] `MemSearchBackend` adapter in the backend adapter layer
- [ ] UI: backend selector (LightMem / MemSearch) in memory config panel
- [ ] Migration utility: export LightMem memories → MemSearch markdown files

---

## 7. Open Questions for Discussion

1. **Storage backend for Agent Studio production:**
   The Qdrant CAI Application approach works but adds an ops burden. Should we explore
   embedding a lighter vector store (e.g. Qdrant embedded mode, or even sqlite-vec) that
   can survive pod restarts via a mounted PVC, rather than requiring a separately deployed
   Qdrant app?

2. **Embedding model dependency:**
   LightMem currently requires an OpenAI-compatible embedding endpoint. In an air-gapped
   or CDEP (on-prem) deployment, this may not be available. Should we add a
   `LIGHTMEM_EMBEDDING_MODEL = "local"` path using a bundled sentence-transformers model, or
   is the OpenAI-compatible requirement acceptable for all target deployments?

3. **Multi-user memory isolation:**
   The current design isolates by workflow (collection_name). In a deployed workflow used by
   multiple end-users, memories from user A could influence user B's responses. Do we need a
   `USER_ISOLATED` session scope mode where `filters = {"user_id": ...}` is auto-injected?
   This requires the deployment runtime to pass a user identifier.

4. **Memory schema enforcement:**
   The `note_schema` field in `MemoryConfig` is a free-text template injected into agent
   backstories. This relies on the LLM following the schema — it won't always. Should we add
   optional schema validation at the engine's `AUTO_WRITE` layer (e.g. validate the stored
   content matches a JSON schema before writing)?

5. **MemSearch priority:**
   Given MemSearch lacks an MCP server today, Phase 4 depends on either upstream work or
   building a thin FastMCP wrapper ourselves. Is the markdown-first auditability benefit
   important enough to justify building that wrapper now, or should we defer entirely to
   Phase 4?
