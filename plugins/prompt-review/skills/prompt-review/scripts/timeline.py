#!/usr/bin/env python3
"""Build the files of the evolution dashboard: weekly numbers, past reviews, and the page.

Claude Code deletes transcripts after cleanupPeriodDays (30 by default), so the stored weeks
are the only record of anything older. They live in two places, the dashboard and a local
copy in ~/.claude/prompt-review, and every run merges both: losing either one loses nothing.
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "dashboard.html")
HISTORY = os.path.expanduser("~/.claude/prompt-review")
PAGE_VERSION = re.compile(r'<meta name="prompt-review-page" content="(\d+)"')
# The weekly columns a review may track. Score and clean share go up as prompting improves;
# every rate goes down.
METRICS = ("score", "clean_share", "friction_rate", "clarify_rate", "interrupt_rate",
           "correction_rate", "handback_rate", "go_ahead_rate")
RECENT = 4   # complete weeks behind every "then" and "now"


def read_rows(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else data.get("rows", [])


def merge_weeks(stored, fresh, horizon):
    """Each week keeps its fullest copy: a complete one before a partial one, then the one that
    saw more prompts, then the fresh one, which applies the current scoring.

    So a complete copy never gives way to a partial one. A week passing the horizon is
    recomputed as partial, and smaller on every run as its transcripts are deleted; the stored
    copy is the full one. A partial week that ended before the horizon is never taken fresh:
    what is left of it is the few long-lived sessions cleanup has not reached yet, a sample
    that would misstate it."""
    def rank(w, is_fresh):
        return bool(w["complete"]), w["prompts"], is_fresh

    best = {}
    for w in stored:
        if w["week"] not in best or rank(w, False) > rank(best[w["week"]], False):
            best[w["week"]] = w
    kept = set(best)
    for w in fresh:
        ended = datetime.fromisoformat(w["week"]).replace(tzinfo=timezone.utc) + timedelta(days=7)
        if not w["complete"] and ended <= horizon:
            continue
        old = best.get(w["week"])
        if old is None or rank(w, True) > rank(old, False):
            best[w["week"]] = w
            kept.discard(w["week"])
    return [best[k] for k in sorted(best)], len({w["week"] for w in stored}), len(kept)


def merge_reviews(*sources):
    """One review per day; a later source wins the day."""
    by_day = {}
    for rows in sources:
        for r in rows:
            by_day[r["date"]] = r
    return [by_day[k] for k in sorted(by_day)]


def recent(weeks, metric):
    """The metric over the last RECENT complete weeks, each weighted as it was computed."""
    done = [w for w in weeks if w["complete"] and w.get(metric) is not None][-RECENT:]
    weight = "steps" if metric == "score" else "prompts"
    total = sum(w[weight] for w in done)
    return round(sum(w[metric] * w[weight] for w in done) / total, 4) if total else None


def show(metric, v):
    if v is None:
        return "n/a"
    return f"{v:.2f}" if metric == "score" else f"{v:.1%}"


LABELS = {"score": "score", "clean_share": "clean prompts", "friction_rate": "friction",
          "clarify_rate": "early questions", "interrupt_rate": "interrupts",
          "correction_rate": "corrections", "handback_rate": "stopped on a question",
          "go_ahead_rate": "bare go-aheads"}
HIGHER_IS_BETTER = ("score", "clean_share")
CLEAR = 2.5   # standard errors a change must clear to count as real: eight metrics are tested at once


def pooled(weeks):
    """Several complete weeks as one: the score weighted by steps, every rate by prompts."""
    prompts = sum(w["prompts"] for w in weeks)
    steps = sum(w["steps"] for w in weeks)
    out = {"weeks": len(weeks), "prompts": prompts, "first": weeks[0]["week"], "last": weeks[-1]["week"]}
    out["score"] = sum(w["score"] * w["steps"] for w in weeks) / steps
    ses = [w.get("score_se") for w in weeks]
    out["score_se"] = (math.sqrt(sum((w["steps"] / steps * se) ** 2 for w, se in zip(weeks, ses)))
                       if None not in ses else None)
    for m in METRICS[1:]:
        out[m] = sum(w[m] * w["prompts"] for w in weeks) / prompts
    return out


def compare(before, after):
    """Every tracked number on two windows of complete weeks that must not overlap."""
    then, now = pooled(before), pooled(after)
    rows = []
    for m in METRICS:
        a, b = then[m], now[m]
        if m == "score":
            se = math.hypot(then["score_se"], now["score_se"]) if None not in (then["score_se"], now["score_se"]) else None
        else:
            se = math.sqrt(a * (1 - a) / then["prompts"] + b * (1 - b) / now["prompts"])
        good = None if not se else ((b - a) / se) * (1 if m in HIGHER_IS_BETTER else -1)
        verdict = ("untested" if good is None else "better" if good >= CLEAR
                   else "worse" if good <= -CLEAR else "within noise")
        rows.append({"metric": m, "then": a, "now": b, "z": good, "verdict": verdict})
    return then, now, rows


def progress(weeks):
    """What changed beyond noise since tracking started: the first RECENT complete weeks the
    history holds against the last RECENT. Until there are 2 * RECENT, the older half against
    the newer half, so the two never overlap."""
    done = [w for w in weeks if w["complete"]]
    if len(done) < 2:
        return None, None, []
    first = min(RECENT, len(done) // 2)
    return compare(done[:first], done[max(len(done) - RECENT, first):])


def ended(w):
    return (datetime.fromisoformat(w["week"]) + timedelta(days=7)).date().isoformat()


def followup(weeks, review):
    """Whether a review's change took: its number over the RECENT complete weeks before the
    review against the complete weeks since, the last RECENT of them."""
    done = [w for w in weeks if w["complete"]]
    before = [w for w in done if ended(w) <= review["date"]][-RECENT:]
    after = [w for w in done if w["week"] >= review["date"]][-RECENT:]
    out = {"weeks": len(after), "then": None, "now": None, "z": None, "verdict": "too soon"}
    if before and after:
        row = next(r for r in compare(before, after)[2] if r["metric"] == review["metric"])
        out.update(then=row["then"], now=row["now"], z=row["z"], verdict=row["verdict"])
    elif before:
        out["then"] = pooled(before)[review["metric"]]
    return out


def window(p):
    return {"first": p["first"], "last": p["last"], "weeks": p["weeks"], "prompts": p["prompts"]}


def rounded(row):
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in row.items()}


def leaks_of(review):
    """A review's leaks as [{name, metric}]: from its suggestions, or from the leaks older
    reviews kept, as a list or as joined text."""
    if review.get("suggestions"):
        return [{"name": s["fixes"], "metric": s.get("metric")} for s in review["suggestions"]]
    leaks = review.get("leaks") or []
    if isinstance(leaks, str):
        leaks = [l for l in leaks.split(" · ") if l.strip()]
    return [l if isinstance(l, dict) else {"name": l, "metric": None} for l in leaks]


def print_progress(weeks, last):
    then, now, rows = progress(weeks)
    if not rows:
        print("progress since tracking started: too soon to tell, fewer than two complete weeks")
        return
    print(f"progress since tracking started: {now['weeks']} complete week(s) from {now['first']} "
          f"({now['prompts']} prompts) against the first {then['weeks']} from {then['first']} "
          f"({then['prompts']} prompts). A change counts once it clears {CLEAR} standard errors.")
    tags = {}
    if last:
        tags[last["metric"]] = "the change you committed to"
        for leak in leaks_of(last):
            if leak.get("metric"):
                tags.setdefault(leak["metric"], f"leak: {leak['name']}")
    order = {"better": 0, "worse": 1, "within noise": 2, "untested": 3}
    for r in sorted(rows, key=lambda r: order[r["verdict"]]):
        m = r["metric"]
        delta = (f"{r['now'] - r['then']:+.2f}" if m == "score"
                 else f"{(r['now'] - r['then']) * 100:+.1f} pts")
        z = f", {r['z']:+.1f} SE" if r["z"] is not None else ""
        tag = f"   <- {tags[m]}" if m in tags else ""
        print(f"  {r['verdict']:12} {LABELS[m]:22} {show(m, r['then'])} -> {show(m, r['now'])}  ({delta}{z}){tag}")


def print_followup(review):
    f = review["followup"]
    m = review["metric"]
    print(f"last review {review['date']}: {review['change']!r}\n  {LABELS[m]}: {show(m, f['then'])} in the "
          f"{RECENT} complete weeks before it", end="")
    if f["verdict"] == "too soon":
        print("; no complete week since it yet, too soon to tell")
    else:
        print(f", {show(m, f['now'])} in the {f['weeks']} since ({f['z']:+.1f} SE): {f['verdict']}")


def page_text(doc):
    """The page's `text`, wherever the saved document nests it."""
    if isinstance(doc, dict):
        if isinstance(doc.get("text"), str):
            return doc["text"]
        for v in doc.values():
            found = page_text(v)
            if found:
                return found
    return ""


def page_version(text):
    m = PAGE_VERSION.search(text or "")
    return int(m.group(1)) if m else 0


def main():
    ap = argparse.ArgumentParser(description="Build the evolution dashboard's files.")
    ap.add_argument("--out", required=True, help="folder to write weeks.json, reviews.json, page.json")
    ap.add_argument("--weeks", help="the dashboard's current weeks file, if it has one")
    ap.add_argument("--reviews", help="the dashboard's current reviews file, if it has one")
    ap.add_argument("--stored-page", help="the dashboard's files/index.html document, saved as JSON")
    ap.add_argument("--review", help="this run's review: JSON with change, metric and suggestions")
    ap.add_argument("--days", type=int, default=30, help="the review's window (default 30)")
    ap.add_argument("--history", default=HISTORY,
                    help=f"the local copy of weeks and reviews, read and rewritten (default {HISTORY})")
    args = ap.parse_args()

    local_weeks = os.path.join(args.history, "weeks.json")
    local_reviews = os.path.join(args.history, "reviews.json")
    # Every transcript still on disk: old weeks fill in from whatever survives.
    records = [r for r in extract.load(3650) if r["outcome"]["assistant_steps"] >= 1]
    weeks, stored, only_stored = merge_weeks(read_rows(local_weeks) + read_rows(args.weeks),
                                             extract.weekly(records), extract.transcript_horizon())
    reviews = merge_reviews(read_rows(local_reviews), read_rows(args.reviews))
    today = datetime.now(timezone.utc).date().isoformat()
    earlier = [r for r in reviews if r["date"] < today]
    last = earlier[-1] if earlier else None

    if args.review:
        with open(args.review, encoding="utf-8") as fh:
            review = json.load(fh)
        if review.get("metric") not in METRICS:
            sys.exit(f"review metric must be one of {', '.join(METRICS)}, got {review.get('metric')!r}")
        suggestions = [s for s in review.get("suggestions") or [] if (s.get("text") or "").strip()]
        if not suggestions:
            sys.exit("review needs suggestions: [{text, fixes, cost, metric}], this week's change first")
        for sug in suggestions:
            if not (sug.get("fixes") or "").strip():
                sys.exit(f"suggestion {sug['text']!r} needs the leak it fixes")
            if sug.get("metric") not in (None, *METRICS):
                sys.exit(f"suggestion metric must be one of {', '.join(METRICS)} or null, got {sug['metric']!r}")
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        window_records = [r for r in records if (extract.parse_ts(r["ts"]) or cutoff) >= cutoff]
        then, now, rows = progress(weeks)
        row = {
            "date": today,
            "days": args.days,
            "prompts": len(window_records),
            "score": round(extract.period_score(window_records), 1) if window_records else None,
            "clean_share": (round(sum(1 for r in window_records if r["score"] >= 10) / len(window_records), 4)
                            if window_records else None),
            "friction_rate": (round(sum(map(extract.friction, window_records)) / len(window_records), 4)
                              if window_records else None),
            "change": review.get("change", "").strip(),
            "metric": review["metric"],
            "then": recent(weeks, review["metric"]),
            # The suggestions and what is better since tracking started, as this review saw
            # them: the dashboard shows the latest review's to whoever it is shared with.
            "suggestions": [{"text": s["text"].strip(), "fixes": s["fixes"].strip(),
                             "cost": (s.get("cost") or "").strip(), "metric": s.get("metric")}
                            for s in suggestions],
            "progress": ({"then": window(then), "now": window(now), "rows": [rounded(r) for r in rows]}
                         if rows else None),
        }
        # One review a day: a second run the same day replaces the first.
        reviews = merge_reviews(reviews, [row])

    # Whether each review's change took, judged again on every run as weeks complete.
    for r in reviews:
        r["followup"] = rounded(followup(weeks, r))

    for folder in (args.out, args.history):
        os.makedirs(folder, exist_ok=True)
        for name, rows in (("weeks.json", weeks), ("reviews.json", reviews)):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False, indent=1)

    with open(PAGE, encoding="utf-8") as fh:
        page = fh.read()
    with open(os.path.join(args.out, "page.json"), "w", encoding="utf-8") as fh:
        json.dump({"text": page}, fh, ensure_ascii=False)
    stored_page = ""
    if args.stored_page and os.path.exists(args.stored_page):
        with open(args.stored_page, encoding="utf-8") as fh:
            stored_page = page_text(json.load(fh))
    shipped_v, stored_v = page_version(page), page_version(stored_page)

    complete = [w for w in weeks if w["complete"]]
    print(f"weeks: {len(weeks)} ({len(complete)} complete); {stored} were stored, "
          f"{only_stored} of them kept as stored because their transcripts are gone or partial")
    print(f"history: also saved to {args.history}")
    if complete:
        now = {m: recent(weeks, m) for m in ("score", "clean_share", "friction_rate", "go_ahead_rate")}
        print(f"last {min(RECENT, len(complete))} complete weeks: score {show('score', now['score'])}, "
              f"clean prompts {show('', now['clean_share'])}, friction {show('', now['friction_rate'])}, "
              f"bare go-aheads {show('', now['go_ahead_rate'])}")
    print_progress(weeks, last)
    if last:
        # The report's opening line: how the last review's change is doing.
        print_followup(last)
    if args.review:
        print(f"review {row['date']}: tracks {row['metric']}, then {show(row['metric'], row['then'])}; "
              f"{len(row['suggestions'])} suggestions")
    print(f"reviews: {len(reviews)}")
    if not stored_page:
        print(f"page: write (new, v{shipped_v})")
    elif stored_v < shipped_v:
        print(f"page: write (stored v{stored_v}, shipped v{shipped_v})")
    else:
        print(f"page: keep (v{stored_v})")


if __name__ == "__main__":
    sys.exit(main())
