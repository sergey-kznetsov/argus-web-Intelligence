from __future__ import annotations

from argus.crawler.agent.base import AgentBackend, AgentResult, AgentTask
from argus.security.urls import UnsafeUrlError


class SequentialAgentBackend:
    """Try bounded AGENT backends in a stable order without parallel model calls."""

    name = "auto"

    def __init__(self, backends: list[AgentBackend]) -> None:
        if not backends:
            raise ValueError("at least one AGENT backend is required")
        self.backends = tuple(backends)
        self.max_steps = max(int(getattr(backend, "max_steps", 1)) for backend in self.backends)

    async def run(self, task: AgentTask) -> AgentResult:
        attempts: list[dict[str, object]] = []
        for backend in self.backends:
            try:
                result = await backend.run(task)
            except UnsafeUrlError:
                raise
            except Exception as exc:
                attempts.append(
                    {
                        "backend": backend.name,
                        "status": "unavailable",
                        "reason_code": "AGENT_RUNTIME_UNAVAILABLE",
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            attempts.append(
                {
                    "backend": backend.name,
                    "status": result.metadata.get("status", "unknown"),
                    "reason_code": result.metadata.get("reason_code")
                    or result.metadata.get("code"),
                }
            )
            result.metadata = {
                **result.metadata,
                "selected_backend": backend.name,
                "fallback_order": [item.name for item in self.backends],
                "attempts": attempts,
            }
            # An access challenge is authoritative. Trying another agent would be an
            # attempted bypass of the same public-source restriction.
            if result.success or result.blocked:
                return result

        return AgentResult(
            success=False,
            data={},
            visited_urls=[],
            actions=[],
            error="all bounded AGENT backends were unavailable or incomplete",
            metadata={
                "backend": self.name,
                "status": "failed",
                "reason_code": "AGENT_FALLBACK_EXHAUSTED",
                "fallback_order": [item.name for item in self.backends],
                "attempts": attempts,
                "agent_output_is_evidence": False,
            },
        )
