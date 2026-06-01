# Native Memory Management — Implementation Status

_Last updated: 2026-05-08_
_Branch: `feature/native_mem_management`_

---

## Overall Progress

| Phase | Description | Status |
|---|---|---|
| Phase 1 (MVP) | Schema + service layer + MCP auto-provisioning + basic UI | ~30% |
| Phase 2 | Engine integration (auto-inject, filtering, backstory) | 0% |
| Phase 3 | Lifecycle & observability (CML jobs, memory inspector UI) | 0% |
| Phase 4 | MemSearch backend adapter (MCP server built standalone) | MCP done |

**Backend decision:** MemSearch (not LightMem). The MemSearch MCP prototype was built first;
the native integration targets MemSearch as the primary backend.

---

## What Is Done

### DB Layer — 100%

| Component | File | Notes |
|---|---|---|
| `WorkflowMemoryConfig` table | `studio/db/model.py:376` | id, workflow_id, enabled, collection_name, auto_inject, session_scope, store_mode, filter_config (JSON), backend_config (JSON), note_schema, created_at, updated_at |
| `Agent.memory_mode` column | `studio/db/model.py:199` | NONE \| READ_ONLY \| WRITE_ONLY \| READ_WRITE, default "NONE" |
| `MCPInstance.is_memory_managed` flag | `studio/db/model.py:157` | Boolean, default False — marks auto-provisioned MCP instances |
| Registered in TABLE_TO_MODEL_REGISTRY | `studio/db/model.py:408` | |

### Alembic Migration — 100%

File: `alembic/versions/2026_04_29_0001_add_native_memory_management.py`

- Creates `workflow_memory_config` table
- Adds `agents.memory_mode` column (backfills existing rows with "NONE")
- Adds `mcp_instances.is_memory_managed` column
- SQLite-safe upgrade/downgrade (skips `DROP COLUMN` on SQLite)

### Proto Definitions — 80%

File: `studio/proto/agent_studio.proto`

- `MemoryFilterConfig` message (dedup_threshold, min/max content length, store_mode)
- `MemoryBackendConfig` message (qdrant_url, api_key, memory_path)
- `WorkflowMemoryConfig` message with `MemoryFilterConfig` and `MemoryBackendConfig` fields
- `GetWorkflowMemoryConfig` + `UpdateWorkflowMemoryConfig` RPCs declared in service
- `memory_mode` field on CreateAgent, UpdateAgent, and Agent response messages

**Missing from proto:**
- `backend_type` discriminator field on `WorkflowMemoryConfig` (`"memsearch"`)
- `ClearWorkflowMemory` RPC (in design doc, not yet added)
- **Proto stubs NOT recompiled** — `agent_studio_pb2_grpc.py` has no memory stubs yet

### MemSearch MCP Server (standalone) — 100%

Directory: `memsearch_mcp/`

| File | Contents |
|---|---|
| `server.py` | 6 FastMCP tools: `get_current_date`, `write_memory`, `search_memory`, `list_memory_files`, `reindex_memory`, `show_config`. Lazy singleton, hybrid dense+BM25 search, date-prefix filtering. |
| `requirements.txt` | `memsearch>=0.1.18`, `fastmcp>=2.0.0` |
| `README.md` | Quickstart for openai/ollama/local/onnx providers, Agent Studio MCP JSON config, CML persistent path guidance, LightMem vs MemSearch comparison table |

The server runs as a long-lived subprocess (stdio transport via `mcp.run()`), consistent with
how Agent Studio launches all MCP templates.

---

## What Is Missing (Phase 1)

### Proto recompile (blocking gate)
Nothing downstream can be tested until `pb2_grpc.py` is regenerated. This is Step 1 and
blocks all service layer work.

### Service layer — 0%

| File | Status | Functions needed |
|---|---|---|
| `studio/workflow/workflow_memory.py` | Does not exist | `get_memory_config(workflow_id, session)`, `upsert_memory_config(workflow_id, request, session)`, `clear_memory_config(workflow_id, session)` |
| `studio/as_mcp/memory_provisioner.py` | Does not exist | `provision_memory_mcp(workflow_id, config, session)`, `deprovision_memory_mcp(workflow_id, session)` |
| `studio/service.py` | Memory RPC handlers not wired | `GetWorkflowMemoryConfig`, `UpdateWorkflowMemoryConfig`, `ClearWorkflowMemory` |

### Bundled MCP template — 0%

Directory `studio-data/mcp_templates/memsearch_memory/` does not exist.
Must follow the existing template seeding pattern and declare:
- `type`: `"PYTHON"`
- `args`: `["python", "{STUDIO_DIR}/memsearch_mcp/server.py"]`
- `env_names`: `MEMSEARCH_MEMORY_PATH`, `MEMSEARCH_MILVUS_URI`, `MEMSEARCH_COLLECTION`, `MEMSEARCH_EMBEDDING_PROVIDER`, `MEMSEARCH_EMBEDDING_API_KEY`, `MEMSEARCH_EMBEDDING_BASE_URL`

### Frontend — 0%

| Component | File | What's needed |
|---|---|---|
| Redux API client | `app/api/workflows/workflowMemoryApi.ts` (new) | `getWorkflowMemoryConfig`, `updateWorkflowMemoryConfig` |
| Workflow memory panel | `app/components/workflowEditor/WorkflowEditorConfigureView.tsx` | Enable/disable toggle, collection name display, backend config fields |
| Per-agent memory_mode selector | `app/components/workflowEditor/WorkflowEditorAgentView.tsx` | Dropdown: NONE / READ_ONLY / WRITE_ONLY / READ_WRITE |

---

## What Is Missing (Phase 2 — Engine Integration)

| File | Function | Description |
|---|---|---|
| `studio/workflow_engine/src/engine/crewai/crew.py` | `_initialize_memory_context(user_input, session_id)` | Calls `search_memory` via MCP at session start if `auto_inject=True`; prepends results to first task context |
| `studio/workflow_engine/src/engine/crewai/crew.py` | `_should_store_memory(content, session_id)` | Layer 1 (length/blocklist) + Layer 2 (semantic dedup via retrieve before write) filtering |
| `studio/workflow_engine/src/engine/crewai/agents.py` | `_build_backstory(agent_db, memory_config)` | Appends note_schema block to backstory for WRITE_ONLY/READ_WRITE agents — **ephemeral wrapper only, do NOT mutate `crew_ai_backstory` in DB** |

---

## Recommended Implementation Sequence

```
Step 1  proto: add backend_type field + ClearWorkflowMemory RPC → recompile stubs
Step 2  studio/workflow/workflow_memory.py   (service CRUD)
Step 3  studio-data/mcp_templates/memsearch_memory/  (bundled template)
Step 4  studio/as_mcp/memory_provisioner.py  (provision/deprovision)
Step 5  studio/service.py                    (wire RPC handlers)
Step 6  app/api/workflows/workflowMemoryApi.ts  (Redux API client)
Step 7  WorkflowEditorConfigureView.tsx + WorkflowEditorAgentView.tsx  (UI)
────── Phase 1 MVP complete — test against acceptance criteria ──────
Step 8  crew.py: _initialize_memory_context + _should_store_memory
Step 9  agents.py: backstory injection (ephemeral wrapper)
```

---

## Phase 1 MVP Acceptance Criteria

1. Enable memory on a workflow via UI → `WorkflowMemoryConfig.enabled=True` persists in DB → a new `MCPInstance` with `is_memory_managed=True` is created for the workflow
2. Agents with `memory_mode=READ_WRITE` have `write_memory` and `search_memory` in their `activated_tools`
3. An agent call to `write_memory(content="test note", heading="test")` creates a dated `.md` file containing that entry
4. A subsequent `search_memory(query="test note")` returns the stored entry
5. Disable memory via UI → the `is_memory_managed` MCPInstance is removed
6. Delete the workflow → no orphan `is_memory_managed` MCPInstances remain

---

## Key Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Proto stubs not recompiled | HIGH | Step 1 is a hard gate — nothing else starts until stubs are verified regenerated |
| MCP lifecycle leaks on workflow deletion | MEDIUM | `deprovision_memory_mcp` is idempotent; check `studio/workflow/workflow.py` delete path for cascade hook |
| Ephemeral memory path on CML | MEDIUM | `provision_memory_mcp` must gate on `backend_config.memory_path` being set to a persistent path; raise `ValueError` if unset or defaulted to `~/.memsearch/memories` |

---

## Open Questions

1. **Who owns the embedding API key?** User-entered per workflow, inherited from a global Studio setting, or derived from the workflow's model config? Blocks UI Step 7.
2. **Is per-session memory isolation needed for Phase 1?** `session_scope = CROSS_SESSION | SESSION_SCOPED` is in the schema but session IDs are not threaded through the engine yet. Acceptable to default cross-session for MVP and defer session scoping to Phase 3.
3. **Downgrade path risk:** current `downgrade()` in the Alembic migration drops the table without deprovisioning `is_memory_managed` MCPInstances first. Acceptable for dev; document for production downgrade procedure.
