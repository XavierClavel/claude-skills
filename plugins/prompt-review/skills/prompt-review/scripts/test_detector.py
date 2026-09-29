#!/usr/bin/env python3
"""Regression cases for the correction detector. Run after touching CORRECTION/NOT_CORRECTION.

Every case here is a real `next_prompt` from the transcripts that the detector once got wrong.
"""
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location(
    "extract", os.path.join(os.path.dirname(__file__), "extract.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CASES = [
    # Agreement and follow-up questions are not corrections.
    ("no issue on this one you're right.\nhowever i have this error", False),
    ("no dto change is needed for photobook controller ?", False),
    ("actually even better: the printing support of the flyer should be", False),
    ("explain 3. is it medium if a customer's photo are printed wrong ?", False),
    ("continue", False),
    ("open pr", False),
    # Real repairs.
    ("no back cover -> empty page", True),
    ("no i meant that behaviour must be the same as before", True),
    ("you are wrong, the orders table exists", True),
    ("no durations, all slots are 90 min", True),
    ("do the opposite, don't send flyers for test orders", True),
    ("wrong, update from master", True),
    ("should not be deleted from the api but from our db", True),
]


def main():
    bad = 0
    for text, want in CASES:
        got = bool(m.CORRECTION.search(text) and not m.NOT_CORRECTION.search(text))
        if got != want:
            bad += 1
            print(f"FAIL want={want} got={got}: {text[:70]!r}")
    print(f"{len(CASES) - bad}/{len(CASES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
