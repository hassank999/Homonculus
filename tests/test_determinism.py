"""P0 exit test.

The milestone is defined by these passing:
  - a 10,000-tick run replays byte-identically from its seed
  - the log can reconstruct any tick's full state
Plus guards that the seed actually matters and the log roundtrips losslessly.
"""

from __future__ import annotations

from homunculus.eventlog import EventLog
from homunculus.events import canonical_bytes
from homunculus.loop import run
from homunculus.replay import Replay


def test_byte_identical_replay_10k():
    a, _ = run(42, 10_000)
    b, _ = run(42, 10_000)
    assert canonical_bytes(a) == canonical_bytes(b)


def test_seed_changes_the_stream():
    a, _ = run(42, 500)
    b, _ = run(43, 500)
    assert canonical_bytes(a) != canonical_bytes(b)


def test_reconstruct_any_tick():
    ticks = 10_000
    checkpoints = {0, 1, 500, 4999, 9999, 10_000}
    events, snaps = run(42, ticks, checkpoints=checkpoints)
    rp = Replay(events)
    assert snaps  # sanity: checkpoints were captured
    for t, live in sorted(snaps.items()):
        assert rp.state_at(t) == live, f"replay != live at tick {t}"


def test_log_roundtrips_losslessly(tmp_path):
    events, _ = run(42, 200)
    log = EventLog(tmp_path / "events.jsonl")
    log.write(events)
    assert log.read() == events


def test_file_write_is_byte_identical(tmp_path):
    """Guards the actual on-disk bytes (LF newlines), not just in-memory events."""
    a, _ = run(7, 1000)
    b, _ = run(7, 1000)
    pa, pb = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    EventLog(pa).write(a)
    EventLog(pb).write(b)
    assert pa.read_bytes() == pb.read_bytes()
    assert canonical_bytes(a) == pa.read_bytes()
