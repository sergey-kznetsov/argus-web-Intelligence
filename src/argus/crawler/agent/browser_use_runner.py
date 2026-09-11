from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict

from argus.config import Settings
from argus.crawler.agent.base import AgentTask
from argus.crawler.agent.browser_use import BrowserUseAgent
from argus.security.urls import UrlGuard

MAX_REQUEST_BYTES = 256 * 1024
RESULT_MARKER = "ARGUS_AGENT_RESULT="


async def _main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Browser Use request exceeds its input budget")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Browser Use request must be an object")

    settings = Settings(browser_use_python=None)
    guard = UrlGuard.from_strings(
        settings.allow_internal_targets,
        deny_values=settings.deny_outbound_hosts,
        public_ports=settings.outbound_public_ports,
    )
    task = AgentTask(
        url=str(payload.get("url") or ""),
        goal=str(payload.get("goal") or ""),
        instruction=str(payload.get("instruction") or ""),
        context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
    )
    result = await BrowserUseAgent(settings, guard, llm_health=None).run(task)
    serialized = json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(RESULT_MARKER + serialized + "\n")
    return 0


def main() -> None:
    try:
        code = asyncio.run(_main())
    except Exception as exc:
        sys.stderr.write(f"Browser Use runner failed: {type(exc).__name__}\n")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
