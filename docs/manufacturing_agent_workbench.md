# Manufacturing AI Agent Workbench

Verified against live code in `kmx-personal` (not README-only claims).

## Goal

Human-in-the-loop agent workflow for manufacturing field mapping:

**Upload → Rulebook retrieval → Mapping proposal → Validation → Evidence → Approve / Modify / Reject → Export**

## Live entrypoints

| Surface | Path |
|---|---|
| UI | `http://127.0.0.1:8000/agent-workbench` |
| API | `/ui/api/agent-workbench/*` |
| Package | `backend/manufacturing_agent/` |
| Samples | `data/agent_workbench/samples/` |
| Mock rulebook | `data/agent_workbench/rulebooks/mock_manufacturing_rulebook_v1.json` |
| Run store | `data/agent_workbench/runs/` (JSON + SQLite index) |

## Workbench usage order (operator)

1. Open `/agent-workbench`.
2. Upload CSV/JSON (or run a sample). Optionally describe equipment/process; leave LLM on or off.
3. Review retrieved rulebook hits, mapping candidates, validation issues, and Evidence fields (hashes, rulebook/model/prompt versions, tool traces).
4. Approve, modify candidates, or reject. Actor string is required (client-supplied identity — **no login/RBAC yet**).
5. Export only after `review_status=approved`. Unapproved / `needs_reapproval` exports return HTTP 400.
6. If source bytes change, call `POST .../runs/{id}/check-source-hash` with the new `raw_hash` to invalidate approval.

## Ollama

- Optional. Checked via HTTP before mapping refine.
- If Ollama is down, the model is missing, or the call fails: `llm_error` is recorded, `used_rule_fallback=true`, workflow continues with rule-based proposals.
- Rule fallback vs LLM is distinguishable on the run payload (`llm_available`, `llm_error`, `used_rule_fallback`, `model_name`, `prompt_version`).

## Mock vs real integrations

| Component | Status | Notes |
|---|---|---|
| JSON mock rulebook | **MOCK** | Test fixture only — not KSA/ISO/IDTA official content |
| Rule retrieval + validation | **REAL** | Adapter interface ready for future KSA source |
| Ollama LLM refine | **PARTIAL** | Wired; depends on local model availability |
| HITL approve/modify/reject + export gate | **REAL** | Persist to disk; re-validate on modify |
| Evidence bundle linked to run | **REAL** | Hashes, versions, traces, review history + diffs |
| Source-hash reapproval | **REAL** | `mark_needs_reapproval_if_hash_changed` + API |
| Actor / permission RBAC | **MISSING** | Actor is a free-text field only |
| Eclipse EDC / DSP / K-EDC | **MOCK** | Export flags only (`future_kedc_adapter`) |
| Full AAS repository write | **PARTIAL** | Existing KMX AAS paths elsewhere; Workbench export is mapping artifact with future AAS flag |
| Identity / Policy engine | **MISSING** | Not wired into Workbench |

## Tests

From repository root (with backend deps installed):

```bash
# PowerShell
cd <repo-root>
.\backend\.venv\Scripts\python.exe -m pytest tests/ -v
# or
python -m pytest tests/ -v
```

Key Workbench suites: `tests/test_agent_workbench.py` (workflow, HITL, Evidence, edge-case synthetic manufacturing samples, API + regression smoke).

## Reused from KMX

- `manufacturing/io_loader.py` for CSV/JSON parsing
- Existing Jinja UI shell
- Ollama HTTP pattern isolated in `manufacturing_agent/llm.py`
- Field/AAS vocabulary inspiration encoded into the **mock** rulebook for retrieval tests

## Future adapters

Approved artifact reserves:

```json
"interface": {
  "future_aas_export": true,
  "future_dcat_export": true,
  "future_kedc_adapter": true
}
```

`RulebookAdapter` allows replacing `JsonRulebookAdapter` with a future KSA content source without rewriting the fixed workflow.
