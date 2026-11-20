# Excerpt from Strata: strata/queue/sqlite.py. Omitted: imports, the logger and module constants (among them S, the TaskStatus alias, the TRANSITIONS table and the MIGRATIONS), the classes InvalidTransition, UsageRow, TaskOutcome and Backoff, the helpers _ts, _dt, _task and open_task_queue, and every method of SqliteTaskQueue except claim, requeue_interrupted and _write. Source is private.
"""The task queue on SQLite.

One database file under `DATA_DIR`. Several workers, in one process or in
several, can share it safely:

- Claiming runs inside `BEGIN IMMEDIATE`, which takes SQLite's write lock, so
  two workers never take the same task and `MAX_PARALLEL_TASKS` holds even when
  they race.
- A failed attempt goes back to the queue with a growing pause (backoff) until
  `MAX_TASK_ATTEMPTS` is used up, then the task fails for good.
- When a worker restarts, the tasks it left "in progress" are put back.
- A task that follows background work (a coding worker, Phase 8) comes back
  to look again later (`later`): not a failed attempt, and its `progress`
  line says where it stands.

Status changes follow `TRANSITIONS`; anything else raises `InvalidTransition`.

Every engine run is also a row in `runs` (when, which task, skill and engine,
tokens and money), so usage can be counted per day and per skill; the
`tasks` columns keep each task's totals. `budget_days` holds the owner's
budget raises and which alerts went out, per day (`strata.ops.budget`).
`owner_settings` keeps choices the bot and the worker both read, such as
"only the Claude subscription". `task_messages` says which task a result
message belongs to, so the owner can answer it by replying.
"""


class SqliteTaskQueue:
    """Implements `TaskQueue` for the bot plus the worker side (claim, finish, retry)."""

    async def claim(
        self, worker_id: str, *, paused_skills: Collection[str | None] = ()
    ) -> Task | None:
        """Take the oldest available task, if the parallel limit allows.

        Tasks the owner did not start (scheduled and system ones) wait while
        their skill is in `paused_skills` (the day's money budget is used up);
        None there stands for tasks not routed to a skill yet. The owner's
        tasks always run.

        Atomic across connections and processes: the check and the update run
        under SQLite's write lock.
        """
        now = self._clock()
        held = [skill for skill in paused_skills if skill is not None]
        pause = ""
        if paused_skills:
            conditions = []
            if held:
                conditions.append(f"skill IN ({', '.join('?' for _ in held)})")
            if None in paused_skills:
                conditions.append("skill IS NULL")
            pause = f"AND NOT (source != 'owner' AND ({' OR '.join(conditions)}))"
        async with self._write() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?", (S.IN_PROGRESS.value,)
            )
            running = (await cursor.fetchone())[0]
            if running >= self.max_parallel:
                return None
            cursor = await db.execute(
                f"""
                SELECT id FROM tasks
                WHERE status = ? AND available_at <= ? {pause}
                ORDER BY id LIMIT 1
                """,
                (S.NEW.value, _ts(now), *held),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await db.execute(
                """
                UPDATE tasks
                SET status = ?, attempts = attempts + 1, claimed_by = ?, claimed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (S.IN_PROGRESS.value, worker_id, _ts(now), _ts(now), row["id"]),
            )
            task_id = row["id"]
        return await self._require(task_id)

    async def requeue_interrupted(self, worker_id: str) -> list[Task]:
        """When a worker starts: its tasks still marked in progress were cut off.

        Each counts as a failed attempt and goes back to the queue (or fails if no
        attempts are left). Only tasks claimed under `worker_id` are touched, so a
        restarting worker never takes back tasks another live worker is running.
        Worker ids must therefore be stable across restarts of the same worker.
        """
        async with self._write() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM tasks WHERE status = ? AND claimed_by = ?",
                (S.IN_PROGRESS.value, worker_id),
            )
            tasks = [_task(row) for row in rows]
            for task in tasks:
                await self._fail(db, task, INTERRUPTED_ERROR, retryable=True)
        if tasks:
            log.warning("Put back %d task(s) interrupted by the last stop", len(tasks))
        return [await self._require(task.id) for task in tasks]

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[aiosqlite.Connection]:
        """A write transaction holding SQLite's write lock for its whole length."""
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                await self._conn.execute("ROLLBACK")
                raise
            await self._conn.execute("COMMIT")
