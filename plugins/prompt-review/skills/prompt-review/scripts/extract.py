#!/usr/bin/env python3
"""Pair every typed prompt in the Claude Code transcripts with what happened next.

The outcome signals (clarifications, corrections, interrupts, turns-to-done) are the
evidence a prompt worked or not. A checklist score cannot see them.
"""
import argparse
import json
import math
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
# A reply that only lets the agent carry on. After a window that ended on a question, it means
# the agent stopped to ask for something the prompt could have granted up front.
GO_AHEAD = re.compile(
    r"^\s*(yes|yep|yeah|ok|okay|sure|go|go ahead|go for it|do it|continue|carry on|proceed"
    r"|oui|vas-y|((rebase|push)\s+(and|then)\s+)?open\s+(a\s+)?pr)\s*[.!]*\s*$",
    re.I,
)

# Searching before the first edit: what a prompt could have cut short by naming the thing the
# agent went looking for. Scratch and temp files are not edits, and a heredoc's body is script,
# not shell, so only its own writes count.
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SEARCH_TOOLS = {"Read", "Grep", "Glob", "LSP", "WebFetch", "WebSearch"}
SEARCH_SHELL = re.compile(
    r"^\s*(?:cd\s+\S+\s*(?:&&|;)\s*)?(?:rtk\s+)?"
    r"(?:grep|rg|find|ls|cat|sed\s+-n|head|tail|wc|tree"
    r"|git\s+(?:log|show|grep|diff|ls-files|ls-tree|blame|status))\b"
)
SHELL_WRITE = re.compile(r"\bsed\s+-i|\bperl\s+-pi|\btee\b|\bgit\s+apply\b|\bapply_patch\b"
                         r"|(?<![\d&<>=-])>{1,2}\s*(?!&|/dev/null)([~./\w$\"'][^\s;|&]*)")
SCRIPT_WRITE = re.compile(r"open\([^)]*,\s*['\"][wa]|\.write_text\(|\.write\(")
SCRATCH = re.compile(r"/tmp/|/private/|scratchpad|/var/folders/")
ABS_PATH = re.compile(r"(?<![\w.$])((?:~|\$HOME|/Users|/home)/[^\s\"'`;|&<>(),]+)")
NOT_A_REPO = ("/.claude/", "/Library/")
SHORTCUT_CALLS = 25            # this many calls before the first edit is worth a look
SHORTCUT_CALLS_ELSEWHERE = 10  # fewer, when they went into another repository

# The unit everywhere below is a STEP: one assistant content block (a thinking block, a text
# block, or one tool call), which is one transcript line. An assistant reply averages ~2.6 of
# them, so a step is finer-grained than a conversational turn -- which is the point, since it
# tracks work done rather than messages sent. Every constant here is calibrated in steps.
# A human round trip costs more than a step: it spends the user's attention, the scarce thing.
# Priced at one HALFLIFE, about a warm prompt's worth of agent work, so each time a prompt makes
# the user come back its score halves. At 3 steps, a month where one prompt in five needed the
# user again still averaged 9.6.
ROUND_TRIP = 12
EARLY = 5   # a clarification later than this is about something that emerged, not the prompt
HALFLIFE = 12   # steps of waste that halve the score
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

    Counted in steps (see ROUND_TRIP above), not in conversational turns.

    Waste is ABSOLUTE, not a share of the window: a prompt that sends the agent 40 turns down
    the wrong path is worse than one caught after 3, so normalising by turns would reward the
    expensive mistakes. Long clean runs stay at 10 -- length is not a fault.
    """
    turns = max(outcome["assistant_steps"], 1)
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


def asked_early(r):
    k = r["outcome"]["turns_before_clarification"]
    return k is not None and k <= EARLY


def friction(r):
    o = r["outcome"]
    return bool(asked_early(r) or o["interrupted_by_user"] or o["next_looks_like_correction"])


def handed_back(r):
    o = r["outcome"]
    return o["ended_on_a_question"] and not o["is_last_in_session"]


def period_score(records):
    """Turn-weighted: the share of the agent work commissioned that landed on target, so one
    400-turn disaster outweighs twenty clean one-liners. None when there is nothing to score."""
    total = waste = 0.0
    for r in records:
        t = max(r["outcome"]["assistant_steps"], 1)
        total += t
        waste += t * (1 - r["score"] / 10)
    return 10 * (1 - waste / total) if total else None


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


def is_edit(name, inp):
    if name in EDIT_TOOLS:
        return not SCRATCH.search(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    if name != "Bash":
        return False
    cmd = inp.get("command", "")
    shell = cmd.split("<<")[0]
    for m in SHELL_WRITE.finditer(shell):
        if not (m.group(1) and SCRATCH.search(m.group(1))):
            return True
    return "<<" in cmd and bool(SCRIPT_WRITE.search(cmd)) and not SCRATCH.search(cmd)


def is_search(name, inp):
    if name in SEARCH_TOOLS or (name == "Agent" and inp.get("subagent_type") == "Explore"):
        return True
    return name == "Bash" and bool(SEARCH_SHELL.match(inp.get("command", "")))


_ROOTS = {}


def repo_of(path):
    """The repository a path belongs to, by its main checkout (a worktree's `.git` file names
    it), so a worktree and its main clone count as one repo. None outside any repository."""
    d = path
    while d and d != "/" and not os.path.isdir(d):
        d = os.path.dirname(d)
    if d in _ROOTS:
        return _ROOTS[d]
    root, cur = None, d
    while cur and cur != "/":
        git = os.path.join(cur, ".git")
        if os.path.isdir(git):
            root = cur
            break
        if os.path.isfile(git):
            try:
                with open(git, encoding="utf-8") as fh:
                    m = re.match(r"gitdir:\s*(.+?)/\.git(?:/|$)", fh.read().strip())
            except OSError:
                m = None
            root = m.group(1) if m else cur
            break
        cur = os.path.dirname(cur)
    _ROOTS[d] = root
    return root


def other_repos(inp, cwd):
    """Repositories a tool call reached that the session isn't working in. A repo whose name is
    part of the working directory is the session's own, which covers worktrees kept elsewhere."""
    home = set(cwd.split(os.sep))
    found = set()
    for p in ABS_PATH.findall(json.dumps(inp)):
        p = os.path.expanduser(p.replace("$HOME", "~", 1))
        if (cwd and p.startswith(cwd)) or any(x in p for x in NOT_A_REPO) or SCRATCH.search(p):
            continue
        root = repo_of(p)
        name = os.path.basename(root) if root else ""
        # A hidden checkout (~/.nvm, ~/.oh-my-zsh) is a tool's install, not a project.
        if name and not name.startswith(".") and name not in home:
            found.add(name)
    return found


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

        turns = tools = interrupts = replies = 0
        seen_msgs = set()
        clarified = False
        turns_to_clarify = None
        turns_to_interrupt = None
        out_tokens = 0
        last_assistant_text = ""
        cwd = d.get("cwd", "")
        before_edit = searches = None   # stay None while no edit has happened
        calls, searched, elsewhere, notes = 0, 0, set(), []
        for d2 in rows[i + 1 : end]:
            if d2.get("isSidechain"):
                continue
            if d2.get("type") == "assistant":
                msg = d2.get("message", {})
                turns += 1
                # One assistant message is split across several transcript lines, one per content
                # block, and every line repeats the whole message's usage -- so dedupe by id.
                mid = msg.get("id")
                if mid is None or mid not in seen_msgs:
                    if mid is not None:
                        seen_msgs.add(mid)
                    replies += 1
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
                        if before_edit is None:
                            inp = b.get("input") or {}
                            if is_edit(b.get("name"), inp):
                                before_edit, searches = calls, searched
                            else:
                                calls += 1
                                searched += is_search(b.get("name"), inp)
                                elsewhere |= other_repos(inp, cwd)
                    elif b.get("type") == "text" and b.get("text", "").strip():
                        last_assistant_text = b["text"]
                        if before_edit is None and len(notes) < 2:
                            notes.append(b["text"].strip()[:200])
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
                    "assistant_steps": turns,
                    "assistant_replies": replies,
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
                    "next_is_go_ahead": bool(nxt and GO_AHEAD.match(nxt)),
                    # Only for prompts that led to an edit: the work spent finding where and how.
                    "calls_before_edit": before_edit,
                    "searches_before_edit": searches,
                    "other_repos_before_edit": sorted(elsewhere) if before_edit is not None else [],
                    "notes_before_edit": notes if before_edit is not None else [],
                    "is_last_in_session": end >= len(rows),
                },
            }
        rec["score"] = score(rec["outcome"])
        out.append(rec)
    return out


def load(days, project=None, max_chars=1200):
    """Every typed prompt of the last `days` days, oldest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records = []
    for path in glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if project and project not in path:
            continue
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < cutoff:
                continue
        except OSError:
            continue
        records.extend(extract(path, max_chars))
    records = [r for r in records if (parse_ts(r["ts"]) or cutoff) >= cutoff]
    records.sort(key=lambda r: r["ts"])
    return records


def transcript_horizon():
    """Claude Code deletes transcripts older than cleanupPeriodDays (30 by default), so anything
    before this instant is only partly on disk.

    The oldest transcript caps it too. Raising the setting doesn't bring back what was already
    deleted, and a new machine has nothing older than its first session."""
    days = 30
    try:
        with open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8") as fh:
            days = int(json.load(fh).get("cleanupPeriodDays", 30))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    horizon = datetime.now(timezone.utc) - timedelta(days=days)
    mtimes = []
    for path in glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            mtimes.append(os.path.getmtime(path))
        except OSError:
            continue
    if mtimes:
        horizon = max(horizon, datetime.fromtimestamp(min(mtimes), timezone.utc))
    return horizon


def weekly(records):
    """One row per week (starting Monday, UTC) with the numbers the evolution dashboard plots.

    `steps` and `prompts` are the weights that combine weeks exactly: the score by steps, the
    rates by prompts. A week is `complete` once it is over and none of it is past the horizon.
    """
    import statistics as stat

    now, horizon = datetime.now(timezone.utc), transcript_horizon()
    weeks = {}
    for r in records:
        ts = parse_ts(r["ts"])
        if ts:
            weeks.setdefault((ts - timedelta(days=ts.weekday())).date(), []).append(r)

    rows = []
    for monday, rs in sorted(weeks.items()):
        start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
        n = len(rs)
        cold = [r["score"] for r in rs if r["seq"] == 1]
        warm = [r["score"] for r in rs if r["seq"] >= 2]
        weights = [max(r["outcome"]["assistant_steps"], 1) for r in rs]
        mu = period_score(rs)
        rows.append({
            "week": monday.isoformat(),
            "prompts": n,
            "steps": sum(weights),
            "score": round(mu, 2),
            # Standard error of that weighted mean, so a change in score can be told from noise.
            "score_se": round(math.sqrt(sum(t * t * (r["score"] - mu) ** 2 for t, r in zip(weights, rs)))
                              / sum(weights), 3),
            "cold_score": round(stat.mean(cold), 2) if cold else None,
            "warm_score": round(stat.mean(warm), 2) if warm else None,
            "clean_share": round(sum(1 for r in rs if r["score"] >= 10) / n, 4),
            "friction_rate": round(sum(map(friction, rs)) / n, 4),
            "clarify_rate": round(sum(map(asked_early, rs)) / n, 4),
            "interrupt_rate": round(sum(1 for r in rs if r["outcome"]["interrupted_by_user"]) / n, 4),
            "correction_rate": round(sum(1 for r in rs if r["outcome"]["next_looks_like_correction"]) / n, 4),
            "handback_rate": round(sum(map(handed_back, rs)) / n, 4),
            "go_ahead_rate": round(
                sum(1 for r in rs if handed_back(r) and r["outcome"]["next_is_go_ahead"]) / n, 4
            ),
            "median_chars": int(stat.median(r["chars"] for r in rs)),
            "cold_prompts": len(cold),
            "complete": start >= horizon and start + timedelta(days=7) <= now,
        })
    return rows


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
    ap.add_argument("--timeline", action="store_true",
                    help="one JSON row per week: score, friction and its parts, go-aheads")
    ap.add_argument("--shortcuts", action="store_true",
                    help="prompts that searched heavily before their first edit, to judge by hand")
    args = ap.parse_args()

    records = load(args.days, args.project, args.max_chars)
    if args.skip_slash:
        records = [r for r in records if not r["slash_command"]]
    records = [r for r in records if r["chars"] >= args.min_chars]

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

    records = [r for r in records if r["outcome"]["assistant_steps"] >= args.min_turns]
    if args.timeline:
        for row in weekly(records):
            print(json.dumps(row))
        return

    if args.shortcuts:
        # Candidates only. Whether the user already knew what the agent went looking for is a
        # judgment the transcript can't make, so nothing here touches the score.
        edits = [r for r in records if r["outcome"]["calls_before_edit"] is not None]

        def heavy(r):
            o = r["outcome"]
            return o["calls_before_edit"] >= SHORTCUT_CALLS or (
                o["other_repos_before_edit"] and o["calls_before_edit"] >= SHORTCUT_CALLS_ELSEWHERE)

        found = sorted(filter(heavy, edits), key=lambda r: -r["outcome"]["calls_before_edit"])
        print(f"{len(edits)} prompts led to an edit; {len(found)} searched heavily first "
              f"({SHORTCUT_CALLS}+ calls, or {SHORTCUT_CALLS_ELSEWHERE}+ reaching another repo)")
        for r in found[: args.top]:
            o = r["outcome"]
            where = f"  other repos: {', '.join(o['other_repos_before_edit'])}" if o["other_repos_before_edit"] else ""
            print(f"\n{o['calls_before_edit']:>3} calls before the first edit, {o['searches_before_edit']} of them "
                  f"searches{where}   {'COLD' if r['seq'] == 1 else 'warm'} {r['ts'][:10]} {r['project'][-40:]}")
            print(f"   prompt: {r['prompt'][:200]!r}")
            for note in o["notes_before_edit"]:
                print(f"   agent:  {note!r}")
            if o["next_prompt"]:
                print(f"   next:   {o['next_prompt'][:120]!r}")
        return

    if args.score:
        import statistics as stat

        if not records:
            print("no prompts in window")
            return
        # Turn-weighted is the headline: see period_score.
        weighted = round(period_score(records), 1)
        mean = round(stat.mean(r["score"] for r in records), 1)

        clean = sum(1 for r in records if r["score"] >= 10) / len(records)
        print(f"PERIOD SCORE  {weighted}/10   (turn-weighted, {args.days}d, n={len(records)})")
        print(f"  clean prompts (needed nothing more from you): {clean:.0%}")
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
                      key=lambda r: -r["outcome"]["assistant_steps"])[: args.top]
        worst = sorted(records, key=lambda r: (r["score"], -r["outcome"]["assistant_steps"]))[: args.top]

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
                print(f"{r['score']:>4}/10 {kind} {o['assistant_steps']:>4}s {' '.join(flags)}")
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
                  f"  med_steps={int(stat.median([r['outcome']['assistant_steps'] for r in rs])):>4}")

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
