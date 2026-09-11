from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from argus.config import Settings
from argus.llm_runtime import LlmConcurrencyGate, ollama_generate_payload, optional_llm_ready


def test_ollama_payload_applies_one_bounded_cpu_profile() -> None:
    settings = Settings()
    payload = ollama_generate_payload(settings, "bounded prompt")

    assert payload == {
        "model": "argus-qwen3:8b-cpu",
        "prompt": "bounded prompt",
        "stream": False,
        "think": False,
        "keep_alive": "60s",
        "options": {
            "num_thread": 2,
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0,
        },
        "format": "json",
    }


def test_llm_concurrency_cannot_exceed_single_model_runtime() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_max_concurrency=2)


def test_enabled_ollama_runtime_is_loopback_only() -> None:
    with pytest.raises(ValidationError):
        Settings(ollama_url="https://models.example.com")

    assert Settings(ollama_url="http://localhost:11434").ollama_url == ("http://localhost:11434")


@pytest.mark.asyncio
async def test_global_gate_serializes_every_llm_component() -> None:
    gate = LlmConcurrencyGate(1)
    active = 0
    observed_peak = 0

    async def call(component: str) -> None:
        nonlocal active, observed_peak
        async with gate.slot(component):
            active += 1
            observed_peak = max(observed_peak, active)
            await asyncio.sleep(0)
            active -= 1

    components = (
        "planner",
        "followup",
        "supervisor",
        "entity",
        "semantic",
        "recipe",
        "stagehand",
        "browser-use",
    )
    await asyncio.gather(*(call(component) for component in components))

    assert observed_peak == 1
    assert gate.snapshot() == {
        "max_concurrency": 1,
        "in_flight": 0,
        "waiting": 0,
        "peak_in_flight": 1,
        "completed": len(components),
    }


@pytest.mark.asyncio
async def test_optional_health_failure_is_fail_open_to_deterministic_path() -> None:
    class BrokenHealth:
        async def check(self):
            raise RuntimeError("offline")

    assert await optional_llm_ready(BrokenHealth()) is False
