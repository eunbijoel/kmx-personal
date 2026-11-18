# Manufacturing AI Agent Workbench

Verified against live code in `kmx-personal`.

## Goal

Human-in-the-loop agent workflow for manufacturing field mapping:

**Upload → Rulebook retrieval → Mapping proposal → Validation → Evidence → Identity-gated Approve / Modify / Reject → Export**

## Live entrypoints

| Surface | Path |
|---|---|
| UI | `http://127.0.0.1:8000/agent-workbench` |
| API | `/ui/api/agent-workbench/*` |
| Package | `backend/manufacturing_agent/` |
| Samples | `data/agent_workbench/samples/` |
| Mock rulebook | `data/agent_workbench/rulebooks/mock_manufacturing_rulebook_v1.json` |
| Field dictionary source | `data/control_plane/field_dictionary.json` |
| Run store | `data/agent_workbench/runs/` (JSON + SQLite index) |

## Workbench usage order (operator)

1. Open `/agent-workbench`.
2. Click **Issue local approver identity** (creates DID + VC with `workbench_roles`).
3. Upload CSV/JSON (or run a sample). Choose rulebook source and LLM on/off.
4. Review retrieved hits, mapping candidates, validation, Evidence.
5. Approve / modify / reject with the Actor DID (and optional VC ID).
6. Export only after `review_status=APPROVED`.
7. If source bytes change, call `POST .../runs/{id}/check-source-hash`.

## Identity / RBAC

| Role | Allowed actions |
|---|---|
| `approver` | approve, modify, reject |
| `reviewer` | modify, reject |
| `admin` | approve, modify, reject |

- Actor must be an active `did:kmx:...` in the KMX DID store.
- Roles come from a non-revoked VC claim: `workbench_roles` / `roles` / `role`.
- Bootstrap helper: `POST /ui/api/agent-workbench/bootstrap-identity`
- Free-text actors like `"user"` are rejected (HTTP 403).

This is DID/VC-backed role checks on the existing KMX identity tables — not a full enterprise IdP/SSO.

## Ollama

```bash
# PowerShell example
$env:OLLAMA_MODEL="gemma3:12b"
$env:OLLAMA_URL="http://localhost:11434/api/generate"
```

- Default preferred model: `gemma3:12b` (override with `OLLAMA_MODEL`).
- Resolver also accepts other installed tags from a short preference list when the preferred model is missing.
- Status: `GET /ui/api/agent-workbench/llm-status`
- If Ollama/model is unavailable: `llm_error` recorded, `used_rule_fallback=true`, workflow continues.

Live LLM pytest (optional):

```bash
python -m pytest tests/test_agent_workbench.py -m integration -v
```

## Rulebook sources

| `KMX_RULEBOOK_SOURCE` | Adapter | Notes |
|---|---|---|
| `field_dictionary` (default) | `FieldDictionaryRulebookAdapter` | KMX control-plane vocabulary. **Not KSA-certified.** |
| `mock` | `JsonRulebookAdapter` | Synthetic MVP fixture |

Concept IDs from field dictionary look like `KMX-FD-temperature_c`.  
Factory: `manufacturing_agent.rulebook.factory.get_rulebook_adapter()`.

A future official KSA content pack can implement `RulebookAdapter` the same way — do not claim certification until a real KSA feed is wired.

## Status summary

| Component | Status |
|---|---|
| Field-dictionary rulebook adapter | **REAL** (platform vocabulary) |
| Mock JSON rulebook | **MOCK** (fixture) |
| Ollama LLM refine | **PARTIAL→REAL** when model installed |
| HITL + Evidence + export gate | **REAL** |
| Identity-gated review roles | **REAL** (DID/VC roles) |
| Eclipse EDC / K-EDC | **MOCK** flags only |
| Official KSA certified content | **MISSING** |

## Tests

```powershell
cd <repo-root>
.\backend\.venv\Scripts\python.exe -m pytest tests/ -v
# optional live LLM
.\backend\.venv\Scripts\python.exe -m pytest tests/test_agent_workbench.py -m integration -v
```
