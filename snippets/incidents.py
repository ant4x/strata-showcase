# Excerpt from Strata: strata/ops/incidents.py. Omitted: imports, the logger and module constants, the classes Kind, Incident and IncidentLog, ProcessGuard.for_config, and the helpers log_tail, last_task, _task_line, _clip and _write_atomic. Source is private.
"""Incidents: what went wrong and when, for the owner and the front desk.

An incident is written when:

- a process crashes: an exception escapes its main loop;
- a process starts after a run that did not end cleanly (killed, out of
  memory, stopped by systemd's watchdog). While a process runs, a marker file
  `DATA_DIR/state/<component>.running` exists; a clean exit removes it, so a
  marker found at start means the last run was cut off. Nothing else can be
  known then, so the incident carries the end of that process's log.

Each incident goes to two places:

- memory, `private/incidents/LATEST.md` plus a dated copy, for the owner to
  read (Obsidian). The personal repository never leaves the server;
- `DATA_DIR/incidents/new/<id>.json`, what the front desk works from. Taking an
  incident is an atomic rename, so the bot and the worker never both report
  the same one.

Tracebacks and log lines can hold anything, so everything is redacted. The
same failure again within an hour (a crash loop) is counted, not written again.
"""


class RunMarker:
    """`DATA_DIR/state/<component>.running` exists while the process runs."""

    def __init__(self, data_dir: Path, component: str) -> None:
        self.path = data_dir / STATE_DIR / f"{component}.running"

    def start(self, now: datetime) -> dict[str, Any] | None:
        """Mark this run. Returns the last run's marker if it did not end cleanly."""
        previous: dict[str, Any] | None = None
        if self.path.exists():
            try:
                previous = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.path, json.dumps({"pid": os.getpid(), "started": now.isoformat()}))
        return previous

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class ProcessGuard:
    """Runs a process's main function and writes the incidents around it."""

    def __init__(
        self,
        log_: IncidentLog,
        component: str,
        *,
        data_dir: Path,
        log_file: Path,
        db_path: Path,
        worker_id: str | None = None,
    ) -> None:
        self.incidents = log_
        self.component = component
        self.marker = RunMarker(data_dir, component)
        self.log_file = log_file
        self.db_path = db_path
        self.worker_id = worker_id

    def run(self, main: Callable[[], T]) -> T:
        now = self.incidents.clock()
        previous = self.marker.start(now)
        if previous is not None:
            started = previous.get("started", "an unknown time")
            self._record(
                Incident.new(
                    self.component,
                    Kind.UNCLEAN_STOP,
                    f"The run started {started} (pid {previous.get('pid', '?')}) ended "
                    "without a clean exit: killed, out of memory, or stopped by the watchdog.",
                    log_tail(self.log_file),
                    at=now,
                    last_task=last_task(self.db_path, self.worker_id),
                )
            )
        try:
            result = main()
        except ConfigError:
            self.marker.clear()  # a settings problem: printed for the owner, not an incident
            raise
        except Exception as exc:
            self._record(
                Incident.new(
                    self.component,
                    Kind.CRASH,
                    f"{type(exc).__name__}: {exc}",
                    "".join(traceback.format_exception(exc)),
                    at=self.incidents.clock(),
                    last_task=last_task(self.db_path, self.worker_id),
                )
            )
            self.marker.clear()
            raise
        except BaseException:
            self.marker.clear()  # Ctrl+C in a terminal: stopped on purpose
            raise
        self.marker.clear()
        return result

    def _record(self, incident: Incident) -> None:
        try:
            asyncio.run(self.incidents.record(incident))
        except Exception:
            log.exception("Could not record incident %s", incident.id)
