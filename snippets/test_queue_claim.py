# Excerpt from Strata: tests/test_queue_sqlite.py. Omitted: imports, the helper _task_in, the OPERATIONS table and the other 26 tests: the bot protocol, enqueueing, reopening and the schema, cancelling and counts, single claims and the parallel limit, retries and usage limits, background work, errors, restarts, status transitions, unknown ids, usage records and the plan. Source is private.
"""SQLite queue: atomic claiming, parallel limit, retries, transitions, restarts."""

S = TaskStatus
OWNER = TaskSource.OWNER


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "data" / "strata.db"


@pytest.fixture
async def queue(db: Path, clock: Clock) -> AsyncIterator[SqliteTaskQueue]:
    q = await SqliteTaskQueue.open(db, max_parallel=10, max_attempts=3, clock=clock)
    yield q
    await q.close()


async def test_concurrent_workers_never_take_the_same_task(db: Path, clock: Clock) -> None:
    # Four separate connections, as if four worker processes shared the file.
    queues = [await SqliteTaskQueue.open(db, max_parallel=100, clock=clock) for _ in range(4)]
    try:
        for i in range(30):
            await queues[0].enqueue(f"task {i}", source=OWNER)

        claims = [queue.claim(f"w{n}") for n, queue in enumerate(queues) for _ in range(15)]
        results = await asyncio.gather(*claims)

        claimed = [task for task in results if task is not None]
        assert len(claimed) == 30
        assert len({task.id for task in claimed}) == 30, "a task was claimed twice"
        assert all(task.attempts == 1 for task in claimed)
    finally:
        for queue in queues:
            await queue.close()


async def test_parallel_limit_holds_under_a_race(db: Path, clock: Clock) -> None:
    queues = [await SqliteTaskQueue.open(db, max_parallel=3, clock=clock) for _ in range(4)]
    try:
        for i in range(10):
            await queues[0].enqueue(f"task {i}", source=OWNER)

        results = await asyncio.gather(*(queue.claim("w") for queue in queues for _ in range(5)))

        assert sum(task is not None for task in results) == 3
        assert (await queues[0].counts())[S.IN_PROGRESS] == 3
    finally:
        for queue in queues:
            await queue.close()


async def test_an_unrouted_task_runs_while_only_routed_skills_are_paused(
    queue: SqliteTaskQueue,
) -> None:
    # A NULL skill made `skill IN (...)` NULL, so the task waited with the paused ones.
    unrouted = await queue.enqueue("check the disk", source=TaskSource.SYSTEM)

    claimed = await queue.claim("w", paused_skills={"research_posts"})

    assert claimed is not None and claimed.id == unrouted.id
