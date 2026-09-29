#!/usr/bin/env python3
"""Pair every typed prompt in the Claude Code transcripts with what happened next.

The outcome signals (clarifications, corrections, interrupts, turns-to-done) are the
evidence a prompt worked or not. A checklist score cannot see them.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from glob import glob

PROJECTS = os.path.expanduser("~/.claude/projects")

# A next prompt matching this is probably the user repairing the previous one.
# A next prompt matching this is the user repairing the previous one. Deliberately narrow:
# a bare "wrong" anywhere matched bug reports about the product, not corrections of the agent.
CORRECTION = re.compile(
    r"^\s*(no|nope|non)\b"
    r"|^\s*(wait|wrong)\b"
    r"|^\s*actually,?\s+(no|not|it|the)\b"
    r"|\bi\s+meant\b|\bnot\s+what\s+i\b|\bthat'?s\s+not\b|\bthis\s+is\s+not\b"
    r"|\byou\s+(are|'?re)\s+wrong\b|\bdo\s+the\s+opposite\b"
    r"|\bnot\s+[^.!?\n]{0,40}\bbut\b"   # corrective contrast: "not from X but from Y"
    r"|\brevert\b|\bundo\b|\broll\s?back\b|\bnot\s+that\b"
    r"|\bje\s+voulais\b|\bpas\s+(ça|ca)\b",
    re.I,
)
# Agreement, a follow-up question, or a change of mind -- none of these indict the prompt.
NOT_CORRECTION = re.compile(
    r"you'?re\s+right|\byou\s+are\s+right\b"
    r"|\bno\s+(issue|problem|worries|need)\b"
    r"|\bnot\s+only\b"
    r"|^\s*(no|nope)\b[^.!?\n]{0,80}\?\s*$",
    re.I,
)
INTERRUPT = re.compile(r"\[Request interrupted by user")

# Waste is counted in turn-equivalents. A human round trip costs more than an agent turn
# because it spends the user's attention, which is the scarce resource.
ROUND_TRIP = 3
EARLY = 5   # a clarification later than this is about something that emerged, not the prompt
HALFLIFE = 12   # turn-equivalents of waste that halve the score
CORRECTION_CAP = 24  # a single correction never voids a long productive session outright
PASTED = re.compile(r"<pasted_content[^>]*>.*?</pasted_content>", re.S)
SYSREMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_NAME = re.compile(r"<command-name>([^<]*)</command-name>")
LOCAL_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def clean(raw):
    """Strip machine noise, keep the human signal. Pastes become a marker, not bulk."""
    t = SYSREMINDER.sub("", raw)
    t = LOCAL_STDOUT.sub("", t)
    t = PASTED.sub(lambda m: f"[pasted {len(m.group(0))} chars]", t)
    return t.strip()


def score(outcome):
    """0-10: how much work this prompt misdirected. 10 means nothing had to be redone.

    Waste is ABSOLUTE, not a share of the window: a prompt that sends the agent 40 turns down
    the wrong path is worse than one caught after 3, so normalising by turns would reward the
    expensive mistakes. Long clean runs stay at 10 -- length is not a fault.
    """
    turns = max(outcome["assistant_turns"], 1)
    wasted = 0.0

    k = outcome["turns_before_clarification"]
    if k is not None and k <= EARLY:
        wasted += k + ROUND_TRIP          # burned those turns, then had to stop and ask

    # How much an interrupt threw away is not knowable -- the agent may have gone wrong on its
    # last turn or its first -- so charge the redirect itself and do not guess the prefix.
    if outcome["interrupted_by_user"]:
        wasted += ROUND_TRIP * min(outcome["interrupted_by_user"], 2)

    if outcome["next_looks_like_correction"]:
        wasted += min(turns, CORRECTION_CAP)   # the window's work has to be redone

    if outcome["ended_on_a_question"] and not outcome["is_last_in_session"]:
        wasted += ROUND_TRIP              # handed back unfinished

    return round(10 * 0.5 ** (wasted / HALFLIFE), 1)


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def read_session(path):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def extract(path, max_chars):
    rows = read_session(path)
    # Index every typed prompt; the window between two of them is the first one's outcome.
    typed = [
        i
        for i, d in enumerate(rows)
        if d.get("type") == "user" and d.get("promptSource") == "typed" and not d.get("isSidechain")
    ]
    out = []
    for n, i in enumerate(typed):
        d = rows[i]
        raw = text_of(d.get("message", {}))
        body = clean(raw)
        if not body:
            continue
        cmd = COMMAND_NAME.search(raw)
        end = typed[n + 1] if n + 1 < len(typed) else len(rows)

        turns = tools = interrupts = 0
        clarified = False
        turns_to_clarify = None
        turns_to_interrupt = None
        out_tokens = 0
        last_assistant_text = ""
        for d2 in rows[i + 1 : end]:
            if d2.get("isSidechain"):
                continue
            if d2.get("type") == "assistant":
                msg = d2.get("message", {})
                turns += 1
                out_tokens += (msg.get("usage") or {}).get("output_tokens", 0) or 0
                for b in msg.get("content", []) or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        tools += 1
                        if b.get("name") == "AskUserQuestion":
                            clarified = True
                            if turns_to_clarify is None:
                                turns_to_clarify = turns
                    elif b.get("type") == "text" and b.get("text", "").strip():
                        last_assistant_text = b["text"]
            elif d2.get("type") == "user":
                if INTERRUPT.search(text_of(d2.get("message", {}))):
                    interrupts += 1
                    if turns_to_interrupt is None:
                        turns_to_interrupt = turns

        nxt = clean(text_of(rows[end].get("message", {}))) if end < len(rows) else ""
        t0, t1 = parse_ts(d.get("timestamp", "")), parse_ts(rows[end].get("timestamp", "")) if end < len(rows) else None

        rec = {
                "ts": d.get("timestamp", ""),
                "project": os.path.basename(os.path.dirname(path)),
                "branch": d.get("gitBranch", ""),
                "session": d.get("sessionId", "")[:8],
                "seq": n + 1,
                "slash_command": cmd.group(1) if cmd else None,
                "chars": len(body),
                "prompt": body[:max_chars],
                "truncated": len(body) > max_chars,
                "outcome": {
                    "assistant_turns": turns,
                    "tool_calls": tools,
                    "output_tokens": out_tokens,
                    "asked_clarifying_question": clarified,
                    # A question at turn 2 indicts the prompt; at turn 90 it is about something later.
                    "turns_before_clarification": turns_to_clarify,
                    "interrupted_by_user": interrupts,
                    "turns_before_interrupt": turns_to_interrupt,
                    "ended_on_a_question": last_assistant_text.rstrip().endswith("?"),
                    "seconds_to_next_prompt": int((t1 - t0).total_seconds()) if t0 and t1 else None,
                    "next_prompt": nxt[:300],
                    "next_looks_like_correction": bool(
                        nxt and CORRECTION.search(nxt[:200]) and not NOT_CORRECTION.search(nxt[:200])
                    ),
                    "is_last_in_session": end >= len(rows),
                },
            }
        rec["score"] = score(rec["outcome"])
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser(description="Extract typed prompts with their outcomes.")
    ap.add_argument("--days", type=int, default=14, help="look back this many days (default 14)")
    ap.add_argument("--limit", type=int, default=60, help="max prompts to emit (default 60)")
    ap.add_argument("--project", help="substring filter on the project dir name")
    ap.add_argument("--min-chars", type=int, default=0, help="skip prompts shorter than this")
    ap.add_argument("--max-chars", type=int, default=1200, help="truncate each prompt at this length")
    ap.add_argument("--min-turns", type=int, default=1,
                    help="skip prompts with fewer assistant turns; 0 keeps in-flight ones (default 1)")
    ap.add_argument("--friction-only", action="store_true", help="only prompts that caused friction")
    ap.add_argument("--skip-slash", action="store_true", help="drop bare slash-command invocations")
    ap.add_argument("--stats", action="store_true", help="print aggregate counts instead of prompts")
    ap.add_argument("--score", action="store_true",
                    help="period score, distribution, and the best/worst prompts")
    ap.add_argument("--top", type=int, default=8, help="how many best and worst to show (default 8)")
    ap.add_argument("--correlate", action="store_true",
                    help="where context actually pays: friction by length, split cold vs warm")
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    records = []
    for path in glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if args.project and args.project not in path:
            continue
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < cutoff:
                continue
        except OSError:
            continue
        records.extend(extract(path, args.max_chars))

    records = [r for r in records if (parse_ts(r["ts"]) or cutoff) >= cutoff]
    if args.skip_slash:
        records = [r for r in records if not r["slash_command"]]
    records = [r for r in records if r["chars"] >= args.min_chars]
    records.sort(key=lambda r: r["ts"])

    def friction(r):
        o = r["outcome"]
        early = o["turns_before_clarification"] is not None and o["turns_before_clarification"] <= 5
        return early or o["interrupted_by_user"] or o["next_looks_like_correction"]

    if args.stats:
        n = len(records) or 1
        print(
            json.dumps(
                {
                    "prompts": len(records),
                    "days": args.days,
                    "projects": len({r["project"] for r in records}),
                    "median_chars": sorted(r["chars"] for r in records)[len(records) // 2] if records else 0,
                    "slash_commands": sum(1 for r in records if r["slash_command"]),
                    "clarifying_question_rate": round(
                        sum(1 for r in records if r["outcome"]["asked_clarifying_question"]) / n, 3
                    ),
                    "interrupt_rate": round(sum(1 for r in records if r["outcome"]["interrupted_by_user"]) / n, 3),
                    "correction_rate": round(
                        sum(1 for r in records if r["outcome"]["next_looks_like_correction"]) / n, 3
                    ),
                    "friction_rate": round(sum(1 for r in records if friction(r)) / n, 3),
                },
                indent=2,
            )
        )
        return

    records = [r for r in records if r["outcome"]["assistant_turns"] >= args.min_turns]
    if args.score:
        import statistics as stat

        if not records:
            print("no prompts in window")
            return
        # Turn-weighted is the headline: it answers "what share of the agent work I commissioned
        # landed on target", so one 400-turn disaster outweighs twenty clean one-liners.
        tw_waste = tw_total = 0.0
        for r in records:
            t = max(r["outcome"]["assistant_turns"], 1)
            tw_total += t
            tw_waste += t * (1 - r["score"] / 10)
        weighted = round(10 * (1 - tw_waste / tw_total), 1)
        mean = round(stat.mean(r["score"] for r in records), 1)

        print(f"PERIOD SCORE  {weighted}/10   (turn-weighted, {args.days}d, n={len(records)})")
        print(f"  unweighted mean per prompt: {mean}/10")
        print(f"  cold openers (seq 1):       {round(stat.mean([r['score'] for r in records if r['seq'] == 1] or [0]), 1)}/10")
        print(f"  warm prompts (seq 2+):      {round(stat.mean([r['score'] for r in records if r['seq'] >= 2] or [0]), 1)}/10")

        print("\ndistribution")
        for lo, hi, lab in [(10, 11, "10/10  clean"), (7, 10, "7-9.9  minor"),
                            (4, 7, "4-6.9  costly"), (0.1, 4, "0.1-3.9 bad"), (-1, 0.1, "0/10   wasted")]:
            n = sum(1 for r in records if lo <= r["score"] < hi)
            print(f"  {lab:<16} {n:>4}  {n / len(records):>5.1%}  {'#' * int(40 * n / len(records))}")

        # Among perfect scores, the best prompt is the one that drove the most clean work.
        best = sorted([r for r in records if r["score"] >= 10],
                      key=lambda r: -r["outcome"]["assistant_turns"])[: args.top]
        worst = sorted(records, key=lambda r: (r["score"], -r["outcome"]["assistant_turns"]))[: args.top]

        def show(title, rs):
            print(f"\n=== {title} ===")
            for r in rs:
                o = r["outcome"]
                flags = []
                if o["turns_before_clarification"] is not None and o["turns_before_clarification"] <= EARLY:
                    flags.append(f"asked@t{o['turns_before_clarification']}")
                if o["turns_before_interrupt"] is not None:
                    flags.append(f"interrupt@t{o['turns_before_interrupt']}")
                if o["next_looks_like_correction"]:
                    flags.append("corrected")
                kind = "COLD" if r["seq"] == 1 else "warm"
                print(f"{r['score']:>4}/10 {kind} {o['assistant_turns']:>4}t {' '.join(flags)}")
                print(f"       {r['prompt'][:180]!r}")
                if o["next_prompt"]:
                    print(f"    -> {o['next_prompt'][:120]!r}")

        show(f"BEST {args.top} (perfect score, most clean work delivered)", best)
        show(f"WORST {args.top}", worst)
        return

    if args.correlate:
        import statistics as stat

        buckets = [("<20 chars", 0, 20), ("20-50", 20, 50), ("50-120", 50, 120),
                   ("120-300", 120, 300), ("300+", 300, 10 ** 9)]

        def line(label, rs):
            if not rs:
                return
            n = len(rs)
            print(f"{label:<20} n={n:<5} friction={sum(map(friction, rs)) / n:>6.1%}"
                  f"  med_turns={int(stat.median([r['outcome']['assistant_turns'] for r in rs])):>4}")

        print(f"TOTAL n={len(records)}  ({args.days}d)\n")
        # A first prompt has no conversation behind it; a later one inherits everything said.
        cold = [r for r in records if r["seq"] == 1]
        warm = [r for r in records if r["seq"] >= 2]
        print("--- cold start vs warm ---")
        line("seq 1 (cold)", cold)
        line("seq 2+ (warm)", warm)
        print("\n--- COLD, by length ---")
        for lab, lo, hi in buckets:
            line("  " + lab, [r for r in cold if lo <= r["chars"] < hi])
        print("\n--- WARM, by length ---")
        for lab, lo, hi in buckets:
            line("  " + lab, [r for r in warm if lo <= r["chars"] < hi])
        print("\n--- shapes ---")
        line("has URL", [r for r in records if "http" in r["prompt"]])
        line("multi-line", [r for r in records if "\n" in r["prompt"]])
        line("single-line", [r for r in records if "\n" not in r["prompt"]])
        return

    if args.friction_only:
        records = [r for r in records if friction(r)]

    # Keep the most recent slice; recent habits are the ones worth changing.
    for r in records[-args.limit :]:
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
