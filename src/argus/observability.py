from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from argus.security.redaction import redact_text

_EXTRA_FIELDS = (
    "event",
    "collection_id",
    "analysis_id",
    "consumer",
    "source_id",
    "stage",
    "status",
    "error_code",
)


class ArgusJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage(), max_length=2000),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact_text(value, max_length=500)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["exception"] = redact_text(
                self.formatException(record.exc_info),
                max_length=4000,
            )
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    logger = logging.getLogger("argus")
    logger.setLevel(level.upper())
    logger.propagate = False
    for handler in logger.handlers:
        if getattr(handler, "_argus_json_handler", False):
            handler.setLevel(level.upper())
            return
    handler = logging.StreamHandler()
    handler.setLevel(level.upper())
    handler.setFormatter(ArgusJsonFormatter())
    handler._argus_json_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)


@dataclass(slots=True)
class _DurationSeries:
    count: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0
    last_seconds: float = 0.0

    def observe(self, seconds: float) -> None:
        value = max(0.0, float(seconds))
        self.count += 1
        self.total_seconds += value
        self.max_seconds = max(self.max_seconds, value)
        self.last_seconds = value

    def as_dict(self) -> dict[str, object]:
        average = self.total_seconds / self.count if self.count else 0.0
        return {
            "count": self.count,
            "total_seconds": round(self.total_seconds, 6),
            "average_seconds": round(average, 6),
            "max_seconds": round(self.max_seconds, 6),
            "last_seconds": round(self.last_seconds, 6),
        }


class OperationalMetrics:
    """Small in-process metric registry with deliberately bounded cardinality.

    Labels must describe stable operational dimensions such as source ID, runtime and
    terminal status. URLs, collection IDs, consumer IDs and arbitrary errors must never
    become labels. The registry is process-local; PostgreSQL queue state is merged by
    the operations endpoint at read time.
    """

    version = "argus-operational-metrics/1"
    max_series_per_metric = 128
    max_labels = 6
    max_label_chars = 80

    def __init__(self) -> None:
        self.started_at = datetime.now(UTC)
        self._started_monotonic = time.monotonic()
        self._lock = threading.RLock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], int]] = defaultdict(dict)
        self._durations: dict[
            str, dict[tuple[tuple[str, str], ...], _DurationSeries]
        ] = defaultdict(dict)
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._dropped_series: dict[str, int] = defaultdict(int)

    def inc(self, name: str, value: int = 1, **labels: object) -> None:
        metric = self._metric_name(name)
        key = self._labels(labels)
        with self._lock:
            series = self._counters[metric]
            if key not in series and len(series) >= self.max_series_per_metric:
                self._dropped_series[metric] += 1
                return
            series[key] = int(series.get(key, 0)) + int(value)

    def observe(self, name: str, seconds: float, **labels: object) -> None:
        metric = self._metric_name(name)
        key = self._labels(labels)
        with self._lock:
            series = self._durations[metric]
            current = series.get(key)
            if current is None:
                if len(series) >= self.max_series_per_metric:
                    self._dropped_series[metric] += 1
                    return
                current = _DurationSeries()
                series[key] = current
            current.observe(seconds)

    def gauge(self, name: str, value: float, **labels: object) -> None:
        metric = self._metric_name(name)
        key = self._labels(labels)
        with self._lock:
            series = self._gauges[metric]
            if key not in series and len(series) >= self.max_series_per_metric:
                self._dropped_series[metric] += 1
                return
            series[key] = float(value)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "version": self.version,
                "started_at": self.started_at.isoformat(),
                "uptime_seconds": round(max(0.0, time.monotonic() - self._started_monotonic), 3),
                "counters": {
                    name: self._counter_rows(series)
                    for name, series in sorted(self._counters.items())
                },
                "durations": {
                    name: self._duration_rows(series)
                    for name, series in sorted(self._durations.items())
                },
                "gauges": {
                    name: self._gauge_rows(series)
                    for name, series in sorted(self._gauges.items())
                },
                "dropped_series": dict(sorted(self._dropped_series.items())),
                "cardinality_policy": {
                    "max_series_per_metric": self.max_series_per_metric,
                    "max_labels": self.max_labels,
                    "collection_id_labels": False,
                    "consumer_labels": False,
                    "url_labels": False,
                },
            }

    @classmethod
    def _metric_name(cls, value: str) -> str:
        normalized = "".join(
            char if char.isalnum() or char in {"_", "."} else "_"
            for char in str(value).strip().casefold()
        )[:128]
        if not normalized:
            raise ValueError("metric name must not be empty")
        return normalized

    @classmethod
    def _labels(cls, labels: dict[str, object]) -> tuple[tuple[str, str], ...]:
        normalized: list[tuple[str, str]] = []
        for key, value in sorted(labels.items())[: cls.max_labels]:
            label = cls._metric_name(str(key))[:64]
            text = str(value if value is not None else "none")
            text = " ".join(text.split())[: cls.max_label_chars]
            normalized.append((label, text or "none"))
        return tuple(normalized)

    @staticmethod
    def _label_dict(key: tuple[tuple[str, str], ...]) -> dict[str, str]:
        return dict(key)

    @classmethod
    def _counter_rows(
        cls,
        series: dict[tuple[tuple[str, str], ...], int],
    ) -> list[dict[str, object]]:
        return [
            {"labels": cls._label_dict(key), "value": value}
            for key, value in sorted(series.items())
        ]

    @classmethod
    def _duration_rows(
        cls,
        series: dict[tuple[tuple[str, str], ...], _DurationSeries],
    ) -> list[dict[str, object]]:
        return [
            {"labels": cls._label_dict(key), **value.as_dict()}
            for key, value in sorted(series.items())
        ]

    @classmethod
    def _gauge_rows(
        cls,
        series: dict[tuple[tuple[str, str], ...], float],
    ) -> list[dict[str, object]]:
        return [
            {"labels": cls._label_dict(key), "value": round(value, 6)}
            for key, value in sorted(series.items())
        ]
