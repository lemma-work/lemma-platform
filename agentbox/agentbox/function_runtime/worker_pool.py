from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import sys
from uuid import UUID

from agentbox.observability import create_background_task, create_inherited_task

from .runtime_models import (
    FunctionSchemaSet,
    WorkerReady,
    WorkerRequest,
    WorkerResponse,
)


class RuntimeOverloaded(RuntimeError):
    pass


class WorkerProtocolError(RuntimeError):
    pass


class WorkerBudget:
    def __init__(self, maximum: int) -> None:
        if maximum < 1:
            raise ValueError("function runtime worker maximum must be positive")
        self.maximum = maximum
        self._reserved = 0
        self._lock = asyncio.Lock()

    async def reserve(self) -> None:
        async with self._lock:
            if self._reserved >= self.maximum:
                raise RuntimeOverloaded(
                    "function runtime worker safety ceiling is exhausted"
                )
            self._reserved += 1

    async def release(self) -> None:
        async with self._lock:
            self._reserved = max(0, self._reserved - 1)


class RevisionWorker:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        stderr_task: asyncio.Task[None],
        budget: WorkerBudget,
        schemas: FunctionSchemaSet | None,
    ) -> None:
        self._process = process
        self._stderr_task = stderr_task
        self._budget = budget
        self._schemas = schemas
        self._closed = False

    @classmethod
    async def start(
        cls,
        root: Path,
        *,
        budget: WorkerBudget,
        deadline_at: datetime,
    ) -> RevisionWorker:
        if deadline_at <= datetime.now(timezone.utc):
            raise TimeoutError("function execution deadline elapsed")
        await budget.reserve()
        process: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[None] | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "agentbox.function_runtime.worker",
                "--serve",
                "--artifact-root",
                str(root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            assert process.stderr is not None
            stderr_task = create_background_task(cls._discard_stderr(process.stderr))
            assert process.stdout is not None
            remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
            line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=max(0.01, remaining),
            )
            if not line:
                raise WorkerProtocolError("revision worker exited before readiness")
            ready = WorkerReady.model_validate_json(line)
            if not ready.ready:
                message = ready.error.message if ready.error is not None else "unknown"
                raise WorkerProtocolError(f"revision worker failed to load: {message}")
            return cls(
                process,
                stderr_task=stderr_task,
                budget=budget,
                schemas=ready.schemas,
            )
        except BaseException:
            if process is not None:
                await cls._stop_process(process)
            if stderr_task is not None:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            await budget.release()
            raise

    async def execute(
        self,
        request: WorkerRequest,
        *,
        deadline_at: datetime,
    ) -> WorkerResponse:
        if self._closed or self._process.returncode is not None:
            raise WorkerProtocolError("revision worker is not running")
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise TimeoutError("function execution deadline elapsed")
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        self._process.stdin.write(request.model_dump_json().encode() + b"\n")
        await self._process.stdin.drain()
        line = await asyncio.wait_for(
            self._process.stdout.readline(),
            timeout=remaining,
        )
        if not line:
            raise WorkerProtocolError("revision worker exited without a response")
        return WorkerResponse.model_validate_json(line)

    @property
    def healthy(self) -> bool:
        return not self._closed and self._process.returncode is None

    @property
    def schemas(self) -> FunctionSchemaSet:
        if self._schemas is None:
            raise WorkerProtocolError("revision worker did not return schemas")
        return self._schemas

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._stop_process(self._process)
        self._stderr_task.cancel()
        await asyncio.gather(self._stderr_task, return_exceptions=True)
        await self._budget.release()

    @staticmethod
    async def _discard_stderr(stream: asyncio.StreamReader) -> None:
        while await stream.read(65536):
            pass

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()


class RevisionWorkerPool:
    def __init__(
        self,
        root: Path,
        *,
        budget: WorkerBudget,
    ) -> None:
        self._root = root
        self._budget = budget
        self._idle: list[RevisionWorker] = []
        self._workers: set[RevisionWorker] = set()
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def acquire(self, *, deadline_at: datetime) -> RevisionWorker:
        bootstrap: asyncio.Task[None] | None = None
        selected: RevisionWorker | None = None
        stale: list[RevisionWorker] = []
        async with self._lock:
            while self._idle:
                worker = self._idle.pop()
                if worker.healthy:
                    selected = worker
                    break
                self._workers.discard(worker)
                stale.append(worker)
            if selected is None and not self._workers:
                if self._bootstrap_task is None:
                    self._bootstrap_task = create_inherited_task(
                        self._bootstrap(deadline_at)
                    )
                bootstrap = self._bootstrap_task
        await asyncio.gather(*(worker.close() for worker in stale))
        if selected is not None:
            return selected
        if bootstrap is not None:
            await asyncio.shield(bootstrap)
            stale = []
            async with self._lock:
                while self._idle:
                    worker = self._idle.pop()
                    if worker.healthy:
                        selected = worker
                        break
                    self._workers.discard(worker)
                    stale.append(worker)
            await asyncio.gather(*(worker.close() for worker in stale))
            if selected is not None:
                return selected
        worker = await RevisionWorker.start(
            self._root,
            budget=self._budget,
            deadline_at=deadline_at,
        )
        async with self._lock:
            self._workers.add(worker)
        return worker

    async def release(self, worker: RevisionWorker) -> None:
        async with self._lock:
            if worker in self._workers and worker.healthy:
                self._idle.append(worker)
                return
            self._workers.discard(worker)
        await worker.close()

    async def discard(self, worker: RevisionWorker) -> None:
        async with self._lock:
            self._workers.discard(worker)
            if worker in self._idle:
                self._idle.remove(worker)
        await worker.close()

    async def reclaim_idle(self, *, limit: int = 1) -> int:
        """Close up to ``limit`` idle workers and return released capacity.

        Active workers are never selected. The registry uses this when another
        revision needs capacity from the sandbox-wide worker budget.
        """

        if limit < 1:
            return 0
        async with self._lock:
            selected = tuple(self._idle[-limit:])
            if selected:
                del self._idle[-len(selected) :]
                for worker in selected:
                    self._workers.discard(worker)
        await asyncio.gather(*(worker.close() for worker in selected))
        return len(selected)

    async def close(self) -> None:
        async with self._lock:
            bootstrap = self._bootstrap_task
            self._bootstrap_task = None
            workers = tuple(self._workers)
            self._workers.clear()
            self._idle.clear()
        if bootstrap is not None and not bootstrap.done():
            bootstrap.cancel()
            await asyncio.gather(bootstrap, return_exceptions=True)
        await asyncio.gather(*(worker.close() for worker in workers))

    async def _bootstrap(self, deadline_at: datetime) -> None:
        task = asyncio.current_task()
        try:
            result = await RevisionWorker.start(
                self._root,
                budget=self._budget,
                deadline_at=deadline_at,
            )
            async with self._lock:
                self._workers.add(result)
                self._idle.append(result)
        finally:
            async with self._lock:
                if self._bootstrap_task is task:
                    self._bootstrap_task = None


class RevisionWorkerRegistry:
    def __init__(
        self,
        *,
        max_workers: int = 32,
        max_cached_revisions: int = 16,
    ) -> None:
        if max_cached_revisions < 1:
            raise ValueError("function runtime revision cache maximum must be positive")
        self._budget = WorkerBudget(max_workers)
        self._max_cached_revisions = max_cached_revisions
        self._pools: OrderedDict[tuple[UUID, str], RevisionWorkerPool] = OrderedDict()
        self._pool_leases: dict[tuple[UUID, str], int] = {}
        self._active: dict[UUID, tuple[RevisionWorkerPool, RevisionWorker]] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        *,
        function_id: UUID,
        revision_hash: str,
        artifact_root: Path,
        run_id: UUID,
        request: WorkerRequest,
        deadline_at: datetime,
    ) -> WorkerResponse:
        key = (function_id, revision_hash)
        evicted: tuple[RevisionWorkerPool, ...] = ()
        async with self._lock:
            pool = self._pools.get(key)
            if pool is None:
                evicted = self._evict_idle_pools_locked(
                    target_size=self._max_cached_revisions - 1
                )
                pool = RevisionWorkerPool(
                    artifact_root,
                    budget=self._budget,
                )
                self._pools[key] = pool
            self._pools.move_to_end(key)
            self._pool_leases[key] = self._pool_leases.get(key, 0) + 1
        await asyncio.gather(*(item.close() for item in evicted))

        worker: RevisionWorker | None = None
        reusable = False
        try:
            worker = await self._acquire_worker(
                pool,
                deadline_at=deadline_at,
            )
            async with self._lock:
                self._active[run_id] = (pool, worker)
            response = await worker.execute(request, deadline_at=deadline_at)
            reusable = True
            return response
        finally:
            async with self._lock:
                self._active.pop(run_id, None)
            if worker is not None:
                if reusable:
                    await pool.release(worker)
                else:
                    await pool.discard(worker)
            await self._release_pool_lease(key)

    async def inspect_schemas(
        self,
        *,
        function_id: UUID,
        revision_hash: str,
        artifact_root: Path,
        deadline_at: datetime,
    ) -> FunctionSchemaSet:
        """Load and retain one revision worker, returning its ready schemas."""

        key = (function_id, revision_hash)
        evicted: tuple[RevisionWorkerPool, ...] = ()
        async with self._lock:
            pool = self._pools.get(key)
            if pool is None:
                evicted = self._evict_idle_pools_locked(
                    target_size=self._max_cached_revisions - 1
                )
                pool = RevisionWorkerPool(
                    artifact_root,
                    budget=self._budget,
                )
                self._pools[key] = pool
            self._pools.move_to_end(key)
            self._pool_leases[key] = self._pool_leases.get(key, 0) + 1
        await asyncio.gather(*(item.close() for item in evicted))

        worker: RevisionWorker | None = None
        try:
            worker = await self._acquire_worker(pool, deadline_at=deadline_at)
            schemas = worker.schemas
            await pool.release(worker)
            worker = None
            return schemas
        finally:
            if worker is not None:
                await pool.discard(worker)
            await self._release_pool_lease(key)

    async def _acquire_worker(
        self,
        pool: RevisionWorkerPool,
        *,
        deadline_at: datetime,
    ) -> RevisionWorker:
        while True:
            try:
                return await pool.acquire(deadline_at=deadline_at)
            except RuntimeOverloaded:
                if not await self._reclaim_one_idle_worker(excluding=pool):
                    raise

    async def _reclaim_one_idle_worker(
        self,
        *,
        excluding: RevisionWorkerPool,
    ) -> bool:
        # OrderedDict iteration is least-recently-used first. Snapshot under the
        # registry lock, then close outside it because process termination is I/O.
        async with self._lock:
            candidates = tuple(
                candidate
                for candidate in self._pools.values()
                if candidate is not excluding
            )
        for candidate in candidates:
            if await candidate.reclaim_idle(limit=1):
                return True
        return False

    async def cancel(self, run_id: UUID) -> bool:
        async with self._lock:
            active = self._active.get(run_id)
        if active is None:
            return False
        pool, worker = active
        await pool.discard(worker)
        return True

    async def close(self) -> None:
        async with self._lock:
            pools = tuple(self._pools.values())
            self._pools.clear()
            self._pool_leases.clear()
            self._active.clear()
        await asyncio.gather(*(pool.close() for pool in pools))

    async def _release_pool_lease(self, key: tuple[UUID, str]) -> None:
        evicted: tuple[RevisionWorkerPool, ...]
        async with self._lock:
            leases = self._pool_leases.get(key, 0)
            if leases <= 1:
                self._pool_leases.pop(key, None)
            else:
                self._pool_leases[key] = leases - 1
            evicted = self._evict_idle_pools_locked(
                target_size=self._max_cached_revisions
            )
        await asyncio.gather(*(pool.close() for pool in evicted))

    def _evict_idle_pools_locked(
        self, *, target_size: int
    ) -> tuple[RevisionWorkerPool, ...]:
        evicted: list[RevisionWorkerPool] = []
        for candidate in tuple(self._pools):
            if len(self._pools) <= target_size:
                break
            if self._pool_leases.get(candidate, 0) != 0:
                continue
            evicted.append(self._pools.pop(candidate))
            self._pool_leases.pop(candidate, None)
        return tuple(evicted)
