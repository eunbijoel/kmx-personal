from __future__ import annotations

import json
import os
from typing import Any

import httpx

OLLAMA_GENERATE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
# Prefer locally common models; override with OLLAMA_MODEL.
DEFAULT_OLLAMA_MODEL = "gemma3:12b"
PREFERRED_OLLAMA_MODELS = (
    os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
    "gemma3:12b",
    "qwen3:14b",
    "llama3.2",
    "mistral",
)
PROMPT_VERSION = "mapping-v1"


def configured_ollama_model() -> str:
    return os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def _model_matches(installed_name: str, wanted: str) -> bool:
    installed = (installed_name or "").strip()
    target = (wanted or "").strip()
    if not installed or not target:
        return False
    return installed == target or installed.startswith(target) or target.startswith(installed.split(":")[0])


def resolve_ollama_model(installed: list[str], *, preferred: str | None = None) -> str | None:
    """Pick first preferred model that appears in Ollama tags."""
    order: list[str] = []
    for name in ((preferred or configured_ollama_model()), *PREFERRED_OLLAMA_MODELS):
        if name and name not in order:
            order.append(name)
    for wanted in order:
        for installed_name in installed:
            if _model_matches(installed_name, wanted):
                return installed_name
    return None


async def check_ollama_available(timeout: float = 3.0) -> dict[str, Any]:
    tags_url = OLLAMA_GENERATE_URL.replace("/api/generate", "/api/tags")
    preferred = configured_ollama_model()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(tags_url)
            response.raise_for_status()
            models = response.json().get("models") or []
            installed = [str(row.get("name") or "") for row in models]
            resolved = resolve_ollama_model(installed, preferred=preferred)
            return {
                "available": True,
                "model_loaded": resolved is not None,
                "model": resolved or preferred,
                "preferred_model": preferred,
                "installed_models": installed,
                "error": None
                if resolved
                else (
                    f"model {preferred} not found in ollama tags "
                    f"(installed={installed or 'none'}; set OLLAMA_MODEL)"
                ),
            }
    except Exception as exc:  # noqa: BLE001 - surface connectivity issues to caller
        return {
            "available": False,
            "model_loaded": False,
            "model": preferred,
            "preferred_model": preferred,
            "installed_models": [],
            "error": str(exc),
        }


async def generate_json(prompt: str, *, timeout: float = 180.0, model: str | None = None) -> dict[str, Any]:
    """Call Ollama and parse JSON. Raises RuntimeError on failure — no fake success."""
    status = await check_ollama_available()
    if not status.get("model_loaded"):
        raise RuntimeError(status.get("error") or "Ollama model not available")
    use_model = model or status.get("model") or configured_ollama_model()
    # Mapping prompts are large; use generous read timeout separately from connect.
    timeout_cfg = httpx.Timeout(timeout, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout_cfg) as client:
        response = await client.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": use_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("response", "{}")
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            raise RuntimeError("LLM response is not a JSON object")
        return parsed


# Back-compat alias used by older imports
OLLAMA_MODEL = configured_ollama_model()
