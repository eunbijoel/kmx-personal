# Manufacturing AI Agent Workbench (MVP)

## Goal

Human-in-the-loop agent workflow for manufacturing field mapping:

Upload → Rulebook retrieval → Mapping proposal → Validation → Evidence → Approve/Modify/Reject → Export

## Live entrypoints

- UI: `/agent-workbench`
- API prefix: `/ui/api/agent-workbench`
- Package: `backend/manufacturing_agent/`

## Reused from KMX

- `manufacturing/io_loader.py` for CSV/JSON parsing
- Existing Jinja UI shell (`base.html`, `app.css`)
- Ollama HTTP pattern similar to `api/routes.py` (isolated in `manufacturing_agent/llm.py`)
- Field/AAS vocabulary inspiration from `field_dictionary.json` / `aas_mapping.json` (encoded into mock rulebook for retrieval tests)

## Mock vs real

| Component | Status |
|---|---|
| JSON mock rulebook | **Mock / test only** — not an official KSA/ISO/IDTA standard |
| Rule retrieval + validation | **Implemented** |
| Ollama LLM mapping refine | **Optional** — fails closed to rule fallback |
| HITL approve/modify/reject + export | **Implemented** |
| Eclipse EDC / DSP / K-EDC negotiation | **Out of scope** (export artifact reserves future adapter flags) |
| Full AAS repository | **Out of scope** |

## Future adapter hooks

Approved artifact includes:

```json
"interface": {
  "future_aas_export": true,
  "future_dcat_export": true,
  "future_kedc_adapter": true
}
```

Rulebook access goes through `RulebookAdapter` so a future KSA content source can replace `JsonRulebookAdapter` without rewriting the workflow.
