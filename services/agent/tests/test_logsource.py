"""Reading the log the agent has always claimed to read.

The landing page says "a thin Python client tails your nginx access log".
Nothing tailed anything. `Collector` batches `LogRecord`s and posts them,
the enforcers apply decisions, the CLI registers - and no code anywhere
turned a line of nginx output into a LogRecord, so the three pieces had
never been connected and `traffic-shaper run` did not exist.

Two decisions here are the ones that matter.

It reads JSON, not the combined format. The shipped example emitted
`$time_local` and a single `"$request"` field while the backend's schema
wants `time_iso8601` and a separate method and URI, so the format on disk
could not have been parsed into the schema it feeds even in principle. A
regex over combined format would have to recover those by splitting on
spaces inside a quoted string, which breaks on the first user agent that
contains a quote. nginx can emit exactly the field names the backend
declares, so it does.

And it starts at the end of the file. A first run that replays an existing
access log would post months of history as though it were happening now:
wrong baseline, wrong decisions, and an ingest bill shaped like an attack.
"""
import json
import os

import pytest

from services.agent.logsource import follow, parse_line


def _next_record(stream, ticks=200):
    """The next record, or a failure - never a hang.

    `follow` yields None forever when there is nothing to read, so a plain
    `next(r for r in stream if r)` turns any regression in it into a test
    run that never ends. A bounded wait fails in a second instead, and says
    which case stopped producing.
    """
    for _ in range(ticks):
        rec = next(stream)
        if rec is not None:
            return rec
    raise AssertionError(f"no record after {ticks} ticks")


def _line(**over):
    row = {"time_iso8601": "2026-09-23T10:00:00+07:00", "remote_addr": "203.0.113.7",
           "request_method": "GET", "request_uri": "/index.html", "status": "200",
           "body_bytes_sent": "1043", "request_time": "0.012",
           "http_user_agent": "curl/8.4.0"}
    row.update(over)
    return json.dumps(row) + "\n"


# --- parsing --------------------------------------------------------------

def test_a_log_line_becomes_a_record():
    rec = parse_line(_line())
    assert rec.remote_addr == "203.0.113.7"
    assert rec.request_method == "GET"
    assert rec.time_iso8601.startswith("2026-09-23T10:00:00")


def test_a_half_written_line_is_skipped_not_fatal():
    """The tail of a live log is regularly a partial line: the agent reads
    between nginx's write and its newline. Raising there would take the
    whole agent down on a routine event."""
    assert parse_line('{"remote_addr": "203.0.1') is None


def test_a_line_missing_a_field_is_skipped(caplog):
    """A customer who edits their log_format must not silently poison the
    baseline with records whose missing fields read as zero."""
    partial = json.loads(_line())
    del partial["status"]
    assert parse_line(json.dumps(partial)) is None


def test_a_blank_line_is_not_an_error():
    assert parse_line("\n") is None
    assert parse_line("") is None


def test_numeric_fields_survive_being_numbers():
    """nginx with escape=json writes $status unquoted. The backend schema
    coerces, and so does this, rather than rejecting the line."""
    rec = parse_line(_line(status=200, body_bytes_sent=1043))
    assert rec.status == "200"
    assert rec.body_bytes_sent == "1043"


def test_a_user_agent_with_quotes_survives():
    """The reason this is JSON and not a regex over the combined format."""
    rec = parse_line(_line(http_user_agent='Mozilla/5.0 (X11; "weird")'))
    assert rec.http_user_agent == 'Mozilla/5.0 (X11; "weird")'


# --- following ------------------------------------------------------------

def test_following_starts_at_the_end_of_an_existing_log(tmp_path):
    """Everything already in the file happened before the agent was
    watching. Replaying it would post months of traffic as though it were
    arriving now."""
    log = tmp_path / "access.log"
    log.write_text(_line(remote_addr="198.51.100.1") * 500)

    stream = follow(log, poll_seconds=0)
    assert next(stream) is None  # nothing new yet: a tick, not a record

    with log.open("a") as fh:
        fh.write(_line(remote_addr="203.0.113.9"))
    assert _next_record(stream).remote_addr == "203.0.113.9"


def test_a_log_that_does_not_exist_yet_is_waited_for(tmp_path):
    """nginx may not have been restarted yet. Exiting here would mean the
    agent dies on install and the customer sees nothing at all."""
    log = tmp_path / "later.log"
    stream = follow(log, poll_seconds=0)
    assert next(stream) is None

    log.write_text(_line(remote_addr="203.0.113.5"))
    assert _next_record(stream).remote_addr == "203.0.113.5"


def test_copytruncate_rotation_is_followed(tmp_path):
    """logrotate's copytruncate mode: same file, emptied in place. An agent
    holding its position past the new end reads nothing ever again, and the
    only symptom is a console reporting that the traffic looks clean."""
    log = tmp_path / "access.log"
    log.write_text(_line() * 20)
    stream = follow(log, poll_seconds=0)
    next(stream)

    log.write_text("")
    log.write_text(_line(remote_addr="203.0.113.77"))

    assert _next_record(stream).remote_addr == "203.0.113.77"


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows refuses to rename a file another handle has open, which "
           "is precisely why logrotate's default mode is a Linux concern. CI "
           "runs this on ubuntu, where the agent actually lives.")
def test_rename_and_create_rotation_is_followed(tmp_path):
    """logrotate's default mode: the file is renamed away and a new one is
    created at the same path. Size cannot see this - the new file can be
    longer than the old position on the very first write - so the file's
    own identity is what is watched."""
    log = tmp_path / "access.log"
    log.write_text(_line() * 3)
    stream = follow(log, poll_seconds=0)
    next(stream)

    log.rename(tmp_path / "access.log.1")
    log.write_text(_line(remote_addr="203.0.113.88") * 40)

    assert _next_record(stream).remote_addr == "203.0.113.88"


def test_a_tick_is_yielded_even_with_nothing_to_read(tmp_path):
    """The run loop flushes on a timer as well as on a full batch, so it
    needs to be handed control when the log is quiet. Otherwise a site with
    two visitors an hour never flushes the first one."""
    log = tmp_path / "access.log"
    log.write_text("")
    stream = follow(log, poll_seconds=0)
    assert next(stream) is None
    assert next(stream) is None


def test_an_unparseable_line_does_not_stop_the_stream(tmp_path):
    log = tmp_path / "access.log"
    log.write_text("")
    stream = follow(log, poll_seconds=0)
    next(stream)

    with log.open("a") as fh:
        fh.write("not json at all\n")
        fh.write(_line(remote_addr="203.0.113.3"))

    rec = next(r for r in stream if r is not None)
    assert rec.remote_addr == "203.0.113.3"
