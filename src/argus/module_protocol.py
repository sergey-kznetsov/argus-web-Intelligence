from __future__ import annotations

from argus import __version__
from argus.contracts.models import PROTOCOL_VERSION

MODULE_ID = "argus.web.intelligence"
DISPLAY_NAME = "ARGUS Web Intelligence"
DESCRIPTION = (
    "Скрытый инфраструктурный backend сбора и доказательного хранения данных "
    "из публичных интернет-источников для серверных аналитических модулей."
)


def runtime_manifest() -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "module_id": MODULE_ID,
        "display_name": DISPLAY_NAME,
        "description": DESCRIPTION,
        "module_version": __version__,
        "capabilities": [
            "infrastructure_service",
            "web_intelligence",
            "evidence_collection",
        ],
        "result_formats": ["json"],
        "supports_partial_result": True,
        "supports_warnings": True,
        "ui": {
            "optional": False,
            "default_enabled": True,
            "analysis_launch_toggle": False,
            "capability_card": False,
        },
    }
