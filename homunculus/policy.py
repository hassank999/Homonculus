"""A hand-coded reactive policy — the P2 exit test, and the P3 control.

No language model, no planning horizon: read the drives, issue one durative
commitment. If drives genuinely ground behaviour, this alone should produce
legible conduct — eat when hungry, warm up when cold, rest when tired.

It stays in the codebase after P3 as the baseline the LLM mind must beat.
Otherwise "the agent did something sensible" is unfalsifiable.

One behaviour here is worth naming because it is not a heuristic hack: when the
agent needs a resource and believes none is available, it goes to LOOK at the
stalest one anyway. Belief about availability decays, and acting to refresh it
is epistemic action in service of a drive. Without it the agent starves beside
food that respawned while it wasn't watching — which is exactly what happened
before this was added.
"""

from __future__ import annotations

LOW_ENERGY = 0.55
LOW_WARMTH = 0.50
HIGH_FATIGUE = 0.55


class ReactivePolicy:
    """Chooses a commitment when the motor is idle. Never called mid-action."""

    name = "reactive"

    def __init__(self):
        # Populated on every choice so the conscious stream can say WHY, not
        # just what. The LLM mind exposes the same two attributes.
        self.last_note = ""
        self.last_prediction = None

    def choose(self, frame, wm, soma, rng):
        d = soma.drives
        energy = d["energy"].value
        warmth = d["warmth"].value
        fatigue = d["fatigue"].value
        self.last_prediction = None

        # 1. Hunger — the most urgent, and the one that requires re-checking.
        if energy < LOW_ENERGY:
            t = self._nearest(frame, wm, ("food",), available=True)
            if t is None:
                # Believe nothing is available. That belief is probably stale,
                # so go look rather than starve.
                t = self._nearest(frame, wm, ("food",), available=None)
            if t:
                b = wm.beliefs.get(t)
                # Must be standing ON it, not merely beside it. Being adjacent
                # is not close enough for the world, and treating it as close
                # enough produced an agent that repeatedly "ate" thin air.
                if b is not None and self._same_cell(b.pos, wm.pose):
                    self.last_note = f"hungry ({energy:.2f}) and standing on {t}"
                    return {"verb": "eat", "target": t}
                stale = b is not None and not b.state.get("available", True)
                self.last_note = (
                    f"hungry ({energy:.2f}); {t} may have regrown, going to look"
                    if stale else f"hungry ({energy:.2f}), heading for {t}"
                )
                return {"verb": "goto", "target": t}

        # 2. Cold — linger once arrived; waiting warms AND rests.
        if warmth < LOW_WARMTH:
            t = self._nearest(frame, wm, ("warmth",))
            if t:
                b = wm.beliefs.get(t)
                if b is not None and self._manhattan(b.pos, wm.pose) <= 1.2:
                    self.last_note = f"cold ({warmth:.2f}), settling by {t}"
                    return {"verb": "wait", "duration": 30}
                self.last_note = f"cold ({warmth:.2f}), heading for {t}"
                return {"verb": "goto", "target": t}

        # 3. Tired.
        if fatigue > HIGH_FATIGUE:
            self.last_note = f"tired ({fatigue:.2f}), resting"
            return {"verb": "wait", "duration": 40}

        # 4. Nothing urgent: re-check the stalest belief. Epistemic action —
        #    moving to reduce uncertainty rather than to satisfy a drive.
        # Weighted by whether the answer will KEEP: verifying a landmark buys
        # lasting certainty, verifying a critter buys one tick of it.
        stale = [
            v for v in frame.entities
            if not v.observed and v.conf < 0.6
            and v.kind in ("landmark", "food", "warmth", "item")
        ]
        if stale:
            stale.sort(key=lambda v: (v.conf, -v.age))
            s = stale[0]
            self.last_note = (
                f"nothing pressing; unsure about {s.id} "
                f"(conf {s.conf:.2f}, unseen {s.age} ticks) - going to look"
            )
            return {"verb": "goto", "target": s.id}

        # Wandering must change the vantage point: somewhere out of sight, or
        # far enough that the walk reveals something. Out-of-sight-only made
        # the agent sedentary; farthest-visible-only made it oscillate.
        cand = [v for v in frame.entities
                if v.kind in ("landmark", "item", "food", "warmth")
                and (not v.observed or v.range >= 6.0)]
        if cand:
            # Least-visited first: exploration is about where you have not
            # BEEN, not what you have not seen.
            cand.sort(key=lambda v: (round(v.visits, 1), v.observed, -v.age, v.id))
            pick = cand[0].id
            self.last_note = f"all needs met; nothing new here, heading for {pick}"
            return {"verb": "goto", "target": pick}
        self.last_note = "all needs met and nothing worth walking to; settling"
        return {"verb": "wait", "duration": 25}

    @staticmethod
    def _manhattan(a, b) -> float:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def _same_cell(a, b) -> bool:
        return (int(round(a[0])), int(round(a[1]))) == (int(round(b[0])), int(round(b[1])))

    @staticmethod
    def _nearest(frame, wm, kinds, available=None):
        best, bestr = None, 1e9
        for v in frame.entities:
            if v.kind not in kinds:
                continue
            if available is not None:
                b = wm.beliefs.get(v.id)
                if b is not None and b.state.get("available", True) != available:
                    continue
            if v.range < bestr:
                best, bestr = v.id, v.range
        return best
