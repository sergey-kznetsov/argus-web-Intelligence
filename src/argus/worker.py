from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
from contextlib import suppress
from uuid import uuid4

from argus import __version__
from argus.bootstrap import build_services
from argus.config import Settings, get_settings
from argus.observability import configure_logging
from argus.storage.lease_fencing import lease_fence
from argus.storage.postgres import PostgresRepository

logger = logging.getLogger("argus.worker")


class CollectionWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        probe_host: str = "127.0.0.1",
        probe_port: int = 0,
    ) -> None:
        if settings.execution_role != "worker":
            raise ValueError("CollectionWorker requires ARGUS_EXECUTION_ROLE=worker")
        if settings.storage_backend != "postgresql":
            raise ValueError("CollectionWorker requires PostgreSQL storage")
        self.settings = settings
        self.services = build_services(settings)
        if not isinstance(self.services.repository, PostgresRepository):
            raise TypeError("CollectionWorker requires PostgresRepository")
        self.repository = self.services.repository
        self.orchestrator = self.services.orchestrator
        self.metrics = self.services.metrics
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
        self.probe_host = probe_host
        self.probe_port = probe_port
        self._stop = asyncio.Event()
        self._started = False
        self._active: dict[str, asyncio.Task[None]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._probe_server: asyncio.AbstractServer | None = None
        self._llm_claims_ready: bool | None = None

    async def start(self) -> None:
        await self.services.start()
        registered = False
        try:
            if self.probe_port:
                self._probe_server = await asyncio.start_server(
                    self._handle_probe,
                    self.probe_host,
                    self.probe_port,
                )

            await self.repository.register_worker(
                self.worker_id,
                metadata={
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "version": __version__,
                },
            )
            registered = True
            self._heartbeat_task = asyncio.create_task(
                self._worker_heartbeat_loop(),
                name=f"argus-worker-heartbeat:{self.worker_id}",
            )
            self._maintenance_task = asyncio.create_task(
                self._maintenance_loop(),
                name=f"argus-worker-maintenance:{self.worker_id}",
            )
            self._started = True
        except BaseException:
            self.metrics.inc("worker_start_errors_total")
            for task in (self._heartbeat_task, self._maintenance_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (self._heartbeat_task, self._maintenance_task)
                    if task is not None
                ),
                return_exceptions=True,
            )
            self._heartbeat_task = None
            self._maintenance_task = None
            if registered:
                with suppress(Exception):
                    await self.repository.unregister_worker(self.worker_id)
            if self._probe_server is not None:
                self._probe_server.close()
                await self._probe_server.wait_closed()
                self._probe_server = None
            await self.services.shutdown()
            raise

        self.metrics.inc("worker_starts_total")
        self.metrics.gauge(
            "worker_active_collections",
            0.0,
            scope="process",
        )
        self.metrics.gauge(
            "worker_concurrency_limit",
            float(self.settings.worker_concurrency),
            scope="process",
        )
        if self.settings.llm_required:
            self._llm_claims_ready = True
            self.metrics.gauge("worker_llm_ready", 1.0, scope="process")
        logger.info(
            "worker started",
            extra={
                "event": "worker_started",
                "worker_id": self.worker_id,
                "concurrency": self.settings.worker_concurrency,
            },
        )

    async def run(self) -> None:
        if not self._started:
            await self.start()
        try:
            while not self._stop.is_set():
                self._reap_finished()
                if not await self._llm_allows_new_claims():
                    await self._idle_wait()
                    continue
                available = self.settings.worker_concurrency - len(self._active)
                claimed = 0
                for _ in range(max(0, available)):
                    collection_id = await self.repository.claim_next_collection(
                        self.worker_id,
                        lease_seconds=self.settings.worker_lease_seconds,
                    )
                    if collection_id is None:
                        break
                    task = asyncio.create_task(
                        self._execute_claim(collection_id),
                        name=f"argus-worker:{collection_id}",
                    )
                    self._active[collection_id] = task
                    claimed += 1
                    self.metrics.inc("worker_claims_total")
                    self.metrics.gauge(
                        "worker_active_collections",
                        float(len(self._active)),
                        scope="process",
                    )
                if claimed == 0:
                    await self._idle_wait()
        finally:
            await self.stop()

    async def _idle_wait(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop.wait(),
                timeout=self.settings.worker_poll_interval_seconds,
            )
        except TimeoutError:
            pass

    async def _llm_allows_new_claims(self) -> bool:
        if not self.settings.llm_required:
            return True
        checker = self.services.llm_health
        if checker is None:
            return self._set_llm_claim_state(False, reason_code="LLM_HEALTH_NOT_CONFIGURED")
        try:
            health = await checker.check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.metrics.inc(
                "worker_llm_health_checks_total",
                status="error",
                error_type=type(exc).__name__,
            )
            return self._set_llm_claim_state(False, reason_code="LLM_HEALTH_CHECK_FAILED")
        self.metrics.inc(
            "worker_llm_health_checks_total",
            status="ready" if health.ready else "degraded",
        )
        return self._set_llm_claim_state(
            bool(health.ready),
            reason_code=health.reason_code,
        )

    def _set_llm_claim_state(self, ready: bool, *, reason_code: str | None) -> bool:
        previous = self._llm_claims_ready
        self._llm_claims_ready = ready
        self.metrics.gauge(
            "worker_llm_ready",
            1.0 if ready else 0.0,
            scope="process",
        )
        if previous is ready:
            return ready
        self.metrics.inc(
            "worker_llm_readiness_transitions_total",
            status="ready" if ready else "degraded",
        )
        if ready:
            logger.info(
                "required local LLM recovered; worker claims resumed",
                extra={
                    "event": "worker_llm_recovered",
                    "worker_id": self.worker_id,
                },
            )
        else:
            logger.warning(
                "required local LLM unavailable; pausing new worker claims",
                extra={
                    "event": "worker_llm_unavailable",
                    "worker_id": self.worker_id,
                    "reason_code": reason_code or "LLM_NOT_READY",
                },
            )
        return ready

    def request_stop(self) -> None:
        self._stop.set()

    async def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        active = list(self._active.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._active.clear()
        self.metrics.gauge("worker_active_collections", 0.0, scope="process")
        if self.settings.llm_required:
            self.metrics.gauge("worker_llm_ready", 0.0, scope="process")

        for attr in ("_heartbeat_task", "_maintenance_task"):
            task = getattr(self, attr)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                setattr(self, attr, None)

        if self._probe_server is not None:
            self._probe_server.close()
            await self._probe_server.wait_closed()
            self._probe_server = None

        with suppress(Exception):
            await self.repository.unregister_worker(self.worker_id)
        await self.services.shutdown()
        self._started = False
        self.metrics.inc("worker_stops_total")
        logger.info(
            "worker stopped",
            extra={"event": "worker_stopped", "worker_id": self.worker_id},
        )

    def _reap_finished(self) -> None:
        for collection_id, task in list(self._active.items()):
            if not task.done():
                continue
            self._active.pop(collection_id, None)
            self.metrics.gauge(
                "worker_active_collections",
                float(len(self._active)),
                scope="process",
            )
            if task.cancelled():
                self.metrics.inc("worker_collection_tasks_total", status="cancelled")
                continue
            error = task.exception()
            if error is not None:
                self.metrics.inc("worker_collection_tasks_total", status="error")
                logger.error(
                    "worker collection task failed",
                    extra={
                        "event": "worker_collection_failed",
                        "worker_id": self.worker_id,
                        "collection_id": collection_id,
                        "error_type": type(error).__name__,
                    },
                )
            else:
                self.metrics.inc("worker_collection_tasks_total", status="ok")

    async def _execute_owned_collection(self, collection_id: str) -> None:
        with lease_fence(collection_id, self.worker_id):
            await self.orchestrator.execute(collection_id)

    async def _execute_claim(self, collection_id: str) -> None:
        execute_task = asyncio.create_task(
            self._execute_owned_collection(collection_id),
            name=f"argus-execute:{collection_id}",
        )
        lease_task = asyncio.create_task(
            self._lease_heartbeat_loop(collection_id),
            name=f"argus-lease:{collection_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {execute_task, lease_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_task in done:
                lease_alive = False
                if not lease_task.cancelled():
                    lease_alive = bool(lease_task.result())
                if not lease_alive and not execute_task.done():
                    self.metrics.inc("worker_execution_cancelled_after_lease_loss_total")
                    execute_task.cancel()
            await execute_task
        finally:
            if not execute_task.done():
                execute_task.cancel()
            if not lease_task.done():
                lease_task.cancel()
            await asyncio.gather(execute_task, lease_task, return_exceptions=True)
            with suppress(Exception):
                await self.repository.release_collection_lease(
                    collection_id,
                    self.worker_id,
                )

    async def _lease_heartbeat_loop(self, collection_id: str) -> bool:
        while not self._stop.is_set():
            await asyncio.sleep(self.settings.worker_heartbeat_seconds)
            renewed = await self.repository.renew_collection_lease(
                collection_id,
                self.worker_id,
                lease_seconds=self.settings.worker_lease_seconds,
            )
            if not renewed:
                self.metrics.inc("worker_lease_losses_total")
                logger.error(
                    "collection lease lost",
                    extra={
                        "event": "worker_lease_lost",
                        "worker_id": self.worker_id,
                        "collection_id": collection_id,
                    },
                )
                return False
            self.metrics.inc("worker_lease_renewals_total")
        return False

    async def _worker_heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(self.settings.worker_heartbeat_seconds)
                alive = await self.repository.heartbeat_worker(self.worker_id)
                if not alive:
                    self.metrics.inc("worker_registration_recoveries_total")
                    await self.repository.register_worker(
                        self.worker_id,
                        metadata={
                            "pid": os.getpid(),
                            "host": socket.gethostname(),
                            "version": __version__,
                        },
                    )
                else:
                    self.metrics.inc("worker_heartbeats_total", status="ok")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.metrics.inc("worker_heartbeats_total", status="error")
                logger.exception(
                    "worker heartbeat failed",
                    extra={
                        "event": "worker_heartbeat_failed",
                        "worker_id": self.worker_id,
                    },
                )

    async def _maintenance_loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self.repository.run_retention(
                    idempotency_window_seconds=self.settings.idempotency_window_seconds,
                    collection_retention_days=self.settings.retention_collection_days,
                    snapshot_retention_days=self.settings.retention_snapshot_days,
                    worker_registration_retention_days=(
                        self.settings.retention_worker_registration_days
                    ),
                    result_access_grace_seconds=(
                        self.settings.retention_result_access_grace_seconds
                    ),
                    batch_size=self.settings.retention_batch_size,
                )
                self.metrics.inc("retention_passes_total", status="ok")
                removed = sum(int(value) for value in result.as_dict().values())
                self.metrics.inc("retention_rows_removed_total", removed)
                if any(result.as_dict().values()):
                    logger.info(
                        "retention pass completed",
                        extra={
                            "event": "retention_completed",
                            "worker_id": self.worker_id,
                            **result.as_dict(),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.metrics.inc("retention_passes_total", status="error")
                logger.exception(
                    "retention pass failed",
                    extra={
                        "event": "retention_failed",
                        "worker_id": self.worker_id,
                    },
                )

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.retention_maintenance_interval_seconds,
                )
            except TimeoutError:
                pass

    async def _probe_llm(self) -> dict[str, object] | None:
        if not self.settings.llm_required:
            return None
        checker = self.services.llm_health
        if checker is None:
            return {
                "status": "unavailable",
                "ready": False,
                "backend": "ollama",
                "model": self.settings.ollama_model,
                "reason_code": "LLM_HEALTH_NOT_CONFIGURED",
            }
        try:
            return (await checker.check()).as_dict()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "status": "unavailable",
                "ready": False,
                "backend": "ollama",
                "model": self.settings.ollama_model,
                "reason_code": "LLM_HEALTH_CHECK_FAILED",
                "error_type": type(exc).__name__,
            }

    async def _handle_probe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        status_code = 503
        payload: dict[str, object] = {
            "status": "degraded",
            "worker_id": self.worker_id,
        }
        try:
            raw = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            first_line = raw.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            parts = first_line.split()
            path = parts[1] if len(parts) >= 2 else ""
            if path == "/metricsz":
                status_code = 200
                payload = {
                    "status": "ok",
                    "metrics": self.metrics.snapshot(),
                }
            elif path not in {"/readyz", "/healthz"}:
                status_code = 404
                payload = {"status": "not_found"}
            else:
                database = await self.repository.health()
                llm = await self._probe_llm()
                database_ready = database.get("status") == "ok"
                llm_ready = llm is None or bool(llm.get("ready"))
                live = self._started and database_ready
                ready = live and llm_ready
                probe_ready = ready if path == "/readyz" else live
                status_code = 200 if probe_ready else 503
                payload = {
                    "status": "ok" if probe_ready else "degraded",
                    "worker_id": self.worker_id,
                    "active_collections": len(self._active),
                    "database": database,
                    "claims_paused": bool(self.settings.llm_required and not llm_ready),
                }
                if llm is not None:
                    payload["llm"] = llm
        except Exception:
            status_code = 503
            payload = {"status": "error"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        reason = "OK" if status_code == 200 else "Not Ready"
        if status_code == 404:
            reason = "Not Found"
        writer.write(
            (
                f"HTTP/1.1 {status_code} {reason}\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            + body
        )
        with suppress(Exception):
            await writer.drain()
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()


async def run_worker(settings: Settings, *, probe_host: str, probe_port: int) -> None:
    worker = CollectionWorker(
        settings,
        probe_host=probe_host,
        probe_port=probe_port,
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, worker.request_stop)
    await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m argus.worker")
    parser.add_argument("--probe-host", default="127.0.0.1")
    parser.add_argument("--probe-port", type=int, default=0)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(
        run_worker(
            settings,
            probe_host=args.probe_host,
            probe_port=args.probe_port,
        )
    )


if __name__ == "__main__":
    main()
