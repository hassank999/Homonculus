"""Turn an event log into a readable conscious stream.

An event log says what happened. A stream says what it was like: what woke the
agent, what it was feeling, what it could see, what it decided and why, what it
expected, and how that turned out.

Narration lives here rather than in the viewer's JavaScript so it is ordinary
testable Python, and so the same stream can be printed to a terminal.

Entry kinds:
  thought   the mind was actually consulted — the expensive path
  habit     acted on the standing plan without thinking
  outcome   an intention finished, one way or another
  percept   something happened TO the agent (bump, meal, thing missing)
  speech    said something aloud
  sleep     offline consolidation ran
"""

from __future__ import annotations

BEARING_WORDS = (
    (-22.5, "ahead"), (22.5, "ahead"), (67.5, "to the right"),
    (112.5, "to the right"), (157.5, "behind"), (180.1, "behind"),
)


def _dirword(bearing: float) -> str:
    b = ((bearing + 180) % 360) - 180
    a = abs(b)
    if a <= 30:
        return "ahead"
    if a >= 150:
        return "behind"
    return "to the right" if b > 0 else "to the left"


def _drive_phrase(because: dict) -> str:
    d, v = because.get("drive"), because.get("value")
    if d is None or v is None:
        return ""
    words = {
        "energy": ("starving", "hungry", "fed"),
        "warmth": ("freezing", "chilly", "warm"),
        "fatigue": ("exhausted", "tiring", "rested"),
    }.get(d, (d, d, d))
    if d == "fatigue":
        w = words[0] if v > 0.8 else words[1] if v > 0.5 else words[2]
    else:
        w = words[0] if v < 0.25 else words[1] if v < 0.55 else words[2]
    return f"{w} ({d} {v:.2f})"


def _saw_phrase(saw: list) -> str:
    if not saw:
        return ""
    bits = []
    for s in saw[:3]:
        if s.get("obs"):
            bits.append(f"{s['id']} {s['range']:.0f} away")
        else:
            bits.append(f"{s['id']} (unsure, {s['age']}t old)")
    return ", ".join(bits)


def _trigger_phrase(gate: str, because: dict) -> str:
    if gate == "surprise":
        sb = because.get("surprised_by") or []
        ex = because.get("existence") or []
        if ex:
            tag = ex[0]
            what = tag[1:]
            how = {"+": "appeared", "-": "was missing", "~": "had changed"}.get(
                tag[0], "was odd"
            )
            return f"something was off: {what} {how}"
        if sb:
            return f"something was off: {sb[0][0]} wasn't where expected"
        return "something was off"
    if gate == "heartbeat":
        return "checking in after a long stretch"
    if gate == "idle":
        return "finished what it was doing"
    return gate or ""


def _action_phrase(dec: dict) -> str:
    v = dec.get("verb")
    t = dec.get("target")
    if v == "goto":
        return f"set off toward {t}"
    if v == "eat":
        return f"ate {t}"
    if v == "wait":
        return f"waited ({dec.get('duration', '?')} ticks)"
    if v == "say":
        return "spoke"
    return v or "?"


def _outcome_phrase(c: dict) -> str:
    verb, status = c.get("verb"), c.get("status")
    tgt = c.get("target")
    ticks = c.get("ticks", 0)
    if status == "done":
        if verb == "goto":
            return f"arrived at {tgt} after {ticks} ticks"
        if verb == "eat":
            return f"finished eating {tgt}"
        if verb == "wait":
            return f"finished waiting ({ticks} ticks)"
        return f"{verb} done"
    reason = {
        "no_path": "no route to it",
        "blocked": "kept hitting something",
        "lost": "lost track of where it was",
        "timeout": "took too long",
        "nothing_here": "nothing there to eat",
        "surprise": "interrupted by something surprising",
        "heartbeat": "interrupted to reconsider",
    }.get(c.get("reason", ""), c.get("reason", "failed"))
    target = f" on {tgt}" if tgt else ""
    return f"gave up{target}: {reason} ({ticks} ticks)"


def _collapse(entries: list[dict]) -> list[dict]:
    """Fold consecutive identical entries into one with a count.

    A habitual agent repeats itself by design — eight identical 'finished
    waiting' lines are accurate but unreadable, and they bury the moments where
    something actually happened.
    """
    out: list[dict] = []
    for e in entries:
        prev = out[-1] if out else None
        if (prev and prev["kind"] == e["kind"] and prev["head"] == e["head"]
                and not e.get("note")):
            prev["count"] = prev.get("count", 1) + 1
            prev["t_end"] = e["t"]
        else:
            out.append(dict(e))
    for e in out:
        if e.get("count", 1) > 1:
            e["head"] = f"{e['head']}  ×{e['count']}"
    return out


def stream(events: list[dict], include_percepts: bool = True,
           collapse: bool = True) -> list[dict]:
    """Produce ordered stream entries from a run's events."""
    out: list[dict] = []

    for ev in events:
        if ev.get("type") != "tick":
            continue
        t = ev["t"]

        dec = ev.get("decision")
        if dec:
            gate = ev.get("gate", "idle")
            because = ev.get("because") or {}
            if gate == "habit":
                out.append({
                    "t": t, "kind": "habit",
                    "head": _action_phrase(dec),
                    "body": "carried on without stopping to think",
                    "note": "",
                })
            else:
                parts = []
                trig = _trigger_phrase(gate, because)
                if trig:
                    parts.append(trig)
                dp = _drive_phrase(because)
                if dp:
                    parts.append(dp)
                sp = _saw_phrase(because.get("saw") or [])
                entry = {
                    "t": t, "kind": "thought",
                    "head": _action_phrase(dec),
                    "body": "; ".join(parts),
                    "saw": sp,
                    "note": ev.get("note", ""),
                    "expect": ev.get("expect", ""),
                    "gate": gate,
                }
                out.append(entry)

        if dec and dec.get("verb") == "say":
            out.append({"t": t, "kind": "speech",
                        "head": f'"{dec.get("text", "")}"', "body": "", "note": ""})

        c = ev.get("commitment")
        if c:
            out.append({"t": t, "kind": "outcome",
                        "head": _outcome_phrase(c), "body": "", "note": ""})

        if include_percepts:
            if ev.get("consumed"):
                out.append({"t": t, "kind": "percept",
                            "head": f"ate {ev['consumed']}",
                            "body": "energy restored", "note": ""})
            surp = ev.get("surprise") or {}
            for tag in (surp.get("existence") or [])[:2]:
                what, mark = tag[1:], tag[0]
                phrase = {"+": f"noticed {what} for the first time",
                          "-": f"{what} was not where it should be",
                          "~": f"{what} had changed"}.get(mark)
                if phrase:
                    out.append({"t": t, "kind": "percept", "head": phrase,
                                "body": "", "note": ""})

        if ev.get("sleep"):
            s = ev["sleep"]
            out.append({"t": t, "kind": "sleep",
                        "head": "consolidated memory",
                        "body": f"{s.get('episodes', 0)} episodes kept, "
                                f"{s.get('facts', 0)} facts formed",
                        "note": ""})

    return _collapse(out) if collapse else out


def to_text(entries: list[dict], limit: int | None = None) -> str:
    """Plain-text rendering, for terminals and tests."""
    lines = []
    for e in entries[:limit] if limit else entries:
        mark = {"thought": "*", "habit": ".", "outcome": "=",
                "percept": "!", "speech": '"', "sleep": "~"}.get(e["kind"], " ")
        lines.append(f"{mark} t{e['t']:<6} {e['head']}")
        if e.get("body"):
            lines.append(f"           {e['body']}")
        if e.get("saw"):
            lines.append(f"           saw: {e['saw']}")
        if e.get("note"):
            lines.append(f'           "{e["note"]}"')
    return "\n".join(lines)
