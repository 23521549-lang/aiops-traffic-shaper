"""Turning nginx output into records, and following the file it arrives in.

The missing link. `Collector` batches records, the enforcers apply
decisions, the CLI registers - and nothing ever turned a line of nginx
output into a record, so the product's own description of itself ("a thin
Python client tails your nginx access log") described code that did not
exist.

JSON, not the combined format. The backend's schema wants `time_iso8601`
and a separate method and URI; the combined format carries `$time_local`
and one `"$request"` string holding method, URI and protocol together.
Recovering those means splitting on spaces inside a quoted field, which is
wrong the first time a user agent contains a quote - and user agents contain
quotes. nginx can emit the backend's own field names directly, so the
example config does that and this reads it.
"""
import json
import logging
import time
from pathlib import Path

from services.agent.collector import LogRecord

logger = logging.getLogger(__name__)

# Exactly the backend's LogRecord. A line missing any of them is dropped
# rather than defaulted: a zero where a status should be is a record that
# poisons the baseline quietly, and quietly is the whole problem.
FIELDS = ("time_iso8601", "remote_addr", "request_method", "request_uri",
          "status", "body_bytes_sent", "request_time", "http_user_agent")

# A log line is metadata about one request. Anything far larger is a broken
# writer or a deliberate one, and it must not be read into memory whole.
MAX_LINE_BYTES = 8192


def parse_line(line: str) -> LogRecord | None:
    """One line to a record, or None.

    Never raises. The tail of a live log is regularly a half-written line,
    caught between nginx's write and its newline, and an exception there
    would take the agent down on a routine event.
    """
    line = line.strip()
    if not line or len(line) > MAX_LINE_BYTES:
        return None
    try:
        row = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(row, dict) or any(f not in row for f in FIELDS):
        return None
    # str() rather than a rejection: nginx with escape=json writes $status
    # and $body_bytes_sent unquoted, and the backend's schema coerces them
    # the same way.
    return LogRecord(**{f: str(row[f]) for f in FIELDS})


def follow(path: Path, poll_seconds: float = 0.5, skip_history: bool = True):
    """Yield records as they are written, and None whenever there is nothing.

    The None matters as much as the records: the run loop flushes on a timer
    as well as on a full batch, so it has to be handed control while the log
    is quiet. Without that, a site with two visitors an hour never sends the
    first one.

    Starts at the end of an existing file. Everything already in it happened
    before the agent was watching, and replaying it would post months of
    traffic as though it were arriving now - a wrong baseline, wrong
    decisions, and an ingest bill shaped like an attack.

    Survives both things that happen to a log file in production: it may not
    exist yet, because nginx has not been restarted since install, and it is
    replaced nightly by logrotate. An agent that exits on the first or holds
    the old handle through the second goes silent, and the only symptom is a
    console reporting that the traffic looks clean.
    """
    path = Path(path)
    handle = None
    position = 0
    signature = None
    # Only a log that already existed has history to skip. If the agent
    # starts before nginx has ever written one, everything that lands in it
    # arrived while we were watching, and skipping to the end would silently
    # drop a brand new customer's first requests.
    skip_history = skip_history and path.exists()
    try:
        while True:
            if handle is None:
                handle = _open(path)
                if handle is None:
                    yield None
                    _sleep(poll_seconds)
                    continue
                # Only ever on the first open. After a rotation the file is
                # new, and skipping to its end would throw away every request
                # logged between the rotation and this poll.
                if skip_history:
                    handle.seek(0, 2)  # history is not news
                    skip_history = False
                position = handle.tell()
                signature = _signature(path)

            line = handle.readline()
            if line:
                position = handle.tell()
                record = parse_line(line)
                yield record  # None for an unreadable line: still a tick
                continue

            if _rotated(path, position, signature):
                handle.close()
                handle = None
                continue

            yield None
            _sleep(poll_seconds)
    finally:
        if handle is not None:
            handle.close()


def _open(path: Path):
    try:
        return path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None


def _signature(path: Path):
    """What identifies this file as distinct from the next one at the path."""
    try:
        st = path.stat()
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def _rotated(path: Path, position: int, signature) -> bool:
    """Gone, emptied, or no longer the same file.

    Both checks are needed and neither is enough alone. copytruncate keeps
    the file and drops its size to zero, which identity cannot see. The
    default rename-and-create mode makes a new file that may already be
    longer than the old position on its first write, which size cannot see.
    """
    try:
        st = path.stat()
    except OSError:
        return True
    if st.st_size < position:
        return True
    return signature is not None and (st.st_dev, st.st_ino) != signature


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
