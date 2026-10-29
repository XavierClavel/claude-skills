#!/usr/bin/env python3
"""Regression cases for the week merge and the progress tests. Run after touching merge_weeks,
transcript_horizon, progress or followup.

The stored weeks are the only record of anything older than the transcripts, so every case
here is a way history could be lost or degraded.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline  # noqa: E402

HORIZON = datetime(2026, 10, 7, tzinfo=timezone.utc)   # falls inside the week of Oct 5


def week(day, prompts, complete, **extra):
    return {"week": day, "prompts": prompts, "complete": complete, **extra}


CASES = [
    # (name, stored, fresh, (complete, prompts) wanted, or None for no row)
    ("a complete week passing the horizon stays complete",
     [week("2026-10-05", 230, True)], [week("2026-10-05", 230, False)], (True, 230)),
    ("a complete week keeps its prompts while its transcripts are deleted",
     [week("2026-10-05", 230, True)], [week("2026-10-05", 90, False)], (True, 230)),
    ("a complete week is rescored when recomputed in full",
     [week("2026-10-12", 243, True, score=9.6)], [week("2026-10-12", 243, True, score=8.9)], (True, 243)),
    ("the current week grows",
     [week("2026-10-26", 100, False)], [week("2026-10-26", 228, False)], (False, 228)),
    ("a fragment of a week before the horizon is not taken",
     [], [week("2026-09-14", 12, False)], None),
    ("a week whose transcripts are gone is kept",
     [week("2026-08-03", 200, True)], [], (True, 200)),
    ("the local copy and the dashboard keep the fuller of the two",
     [week("2026-10-12", 243, True), week("2026-10-12", 120, False)], [], (True, 243)),
]


def full(day, clean):
    """A complete week of 230 prompts with only its clean share set apart from the rest."""
    row = {m: 0.05 for m in timeline.METRICS}
    row.update(week=day, prompts=230, steps=8000, complete=True, score=9.0, score_se=0.1, clean_share=clean)
    return row


TWELVE = [(datetime(2026, 8, 10) + timedelta(weeks=i)).date().isoformat() for i in range(12)]
EIGHT = TWELVE[4:]
# (name, weeks, clean-prompts verdict wanted, or None for "too soon")
PROGRESS = [
    ("a real gain in clean prompts is called better",
     [full(d, 0.70) for d in EIGHT[:4]] + [full(d, 0.85) for d in EIGHT[4:]], "better"),
    ("a few points over two weeks is noise",
     [full(d, 0.73) for d in EIGHT[:2]] + [full(d, 0.76) for d in EIGHT[2:4]], "within noise"),
    ("a drop is called worse",
     [full(d, 0.85) for d in EIGHT[:4]] + [full(d, 0.70) for d in EIGHT[4:]], "worse"),
    ("the last weeks are compared with the first ones tracked, not the ones just before",
     [full(d, 0.70) for d in TWELVE[:4]] + [full(d, 0.85) for d in TWELVE[4:]], "better"),
    ("one complete week is too soon",
     [full(EIGHT[0], 0.70), week(EIGHT[1], 120, False)], None),
]

# (name, weeks, review date, (verdict, complete weeks since) wanted)
FOLLOWUP = [
    ("a change is judged on the complete weeks after its review",
     [full(d, 0.70) for d in EIGHT[:4]] + [full(d, 0.85) for d in EIGHT[4:]], "2026-10-05", ("better", 4)),
    ("the week a review falls in counts for neither side",
     [full(d, 0.70) for d in EIGHT[:4]] + [full(d, 0.85) for d in EIGHT[4:]], "2026-10-07", ("better", 3)),
    ("no complete week since the review is too soon",
     [full(d, 0.70) for d in EIGHT], "2026-10-29", ("too soon", 0)),
]


def main():
    bad = 0
    for name, stored, fresh, want in CASES:
        weeks, *_ = timeline.merge_weeks(stored, fresh, HORIZON)
        got = (weeks[0]["complete"], weeks[0]["prompts"]) if weeks else None
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got}: {name}")
    for name, weeks, want in PROGRESS:
        *_, rows = timeline.progress(weeks)
        got = next((r["verdict"] for r in rows if r["metric"] == "clean_share"), None)
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got}: {name}")
    for name, weeks, day, want in FOLLOWUP:
        f = timeline.followup(weeks, {"date": day, "metric": "clean_share"})
        got = (f["verdict"], f["weeks"])
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got}: {name}")
    total = len(CASES) + len(PROGRESS) + len(FOLLOWUP)
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
