from __future__ import annotations

import json
import os
from typing import Any

import httpx

OLLAMA_GENERATE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")
PROMPT_VERSION = "mapping-v1"


async def check_ollama_available(timeout: float = 3.0) -> dict[str, Any]:
    tags_url = OLLAMA_GENERATE_URL.replace("/api/generate", "/api/tags")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(tags_url)
            response.raise_for_status()
            models = response.json().get("models") or []
            loaded = any(str(row.get("name") or "").startswith(OLLAMA_MODEL) for row in models)
            return {
                "available": True,
                "model_loaded": loaded,
                "model": OLLAMA_MODEL,
                "error": None if loaded else f"model {OLLAMA_MODEL} not found in ollama tags",
            }
    except Exception as exc:  # noqa: BLE001 - surface connectivity issues to caller
        return {
            "available": False,
            "model_loaded": False,
            "model": OLLAMA_MODEL,
            "error": str(exc),
        }


async def generate_json(prompt: str, *, timeout: float = 45.0) -> dict[str, Any]:
    """Call Ollama and parse JSON. Raises RuntimeError on failure — no fake success."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": OLLAMA_MODEL,
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
