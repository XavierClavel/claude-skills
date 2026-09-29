---
name: prompt-review
description: Grade the user's own recent Claude Code prompts, show rewritten before/after versions of the ones that caused friction, and coach them on what to change — judged by what actually happened after each prompt (clarifying questions, corrections, interrupts, turns-to-done) rather than a generic checklist. Use when the user asks to review or rate their prompts, wants better prompt versions, asks how they prompt, wants to improve at prompting, asks whether to give more context, or invokes /prompt-review.
---

# Prompt review

Grade the user's real prompts against what those prompts actually caused, show them the
rewritten version of each one that leaked, and name the small number of habits worth changing.

## Get the data

`scripts/extract.py` walks `~/.claude/projects/*/*.jsonl` and pairs every typed prompt with
the outcome window that follows it (up to the next typed prompt).

```bash
# Works both as an installed plugin and as a loose skill in ~/.claude/skills.
cd "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/skills/prompt-review"

python3 scripts/extract.py --days 30 --score           # 1. period score, best and worst
python3 scripts/extract.py --days 14 --stats           # 1b. aggregate rates
python3 scripts/extract.py --days 30 --correlate       # 2. where context actually pays
python3 scripts/extract.py --days 30 --friction-only   # 3. the prompts to rewrite
python3 scripts/extract.py --days 14 --limit 50        # full slice, only if surveying
python3 scripts/extract.py --days 30 --project myrepo  # one repo
```

**Run 1, 2 and 3 in that order, every time.** They are cheap and they set up the whole report;
skipping them produces generic advice. `--top N` widens the best/worst lists. Use a 30-day window for
`--correlate` so the length buckets have usable n. Never print more than ~50 records.

Each record carries `prompt` and an `outcome`:

| Signal | What it means |
|---|---|
| `turns_before_clarification` | **Use this, not the bare flag.** `<=5` indicts the prompt; `>20` means something emerged later and the prompt is innocent |
| `asked_clarifying_question` | A question was asked *somewhere* in the window — too coarse alone |
| `next_looks_like_correction` | Regex hint only. **Read `next_prompt` yourself** and judge |
| `interrupted_by_user` | The prompt let the agent run the wrong way |
| `ended_on_a_question` | The agent handed work back instead of finishing |
| `assistant_turns` / `tool_calls` | Cost of the prompt |
| `seq` | Position in session. `1` is a cold start and is judged differently — see below |
| `seconds_to_next_prompt` | Long gap + no correction usually means it just worked |

## The score

Every record carries a `score` from 0 to 10, and `--score` prints the period figure, the
distribution, and the best and worst prompts. The model is in `score()` in `extract.py`:

```
waste (turn-equivalents)                     ROUND_TRIP  = 3   a human round trip
  early clarification (turn k <= 5)  k + 3   HALFLIFE    = 12  waste that halves the score
  interrupt                          3 each, max 2       CORRECTION_CAP = 24
  next prompt corrects it            min(turns, 24)
  handed back on a question          3
score = 10 * 0.5 ** (waste / 12)
```

Three properties make it defensible, and you should say so when reporting it:

- **Waste is absolute, not a share of the window.** A prompt that sends the agent 40 turns
  down the wrong path scores worse than one caught after 3. Normalising by turns would have
  rewarded the expensive mistakes.
- **Length and turn count are never penalised.** A 442-turn prompt with no repair is a 10.
  Big clean runs are what good prompts look like.
- **An interrupt is charged as one redirect, not as its whole prefix.** How much an interrupt
  threw away is not knowable — the agent may have gone wrong on its last turn or its first.

**Limits to state in the report, not hide:**

- Most prompts score 10 (typically ~85%). The score discriminates at the bottom, not the top.
  Rank the "best" by clean work delivered, which is what `--score` does.
- A correction is sometimes the *agent's* fault — `"you are wrong, the orders table exists"`
  scores the prompt down for the agent's error. Read every bottom entry and say which ones
  are not the user's fault.
- Corrections are detected by regex, so the friction rate is a floor. `scripts/test_detector.py`
  pins the known cases; **run it after touching `CORRECTION` or `NOT_CORRECTION`.**

## How to grade

**Evidence first, rubric second.** A prompt is good if it got the right work done in few
turns with no repair. That is the only definition that matters.

- **No friction, few turns → the prompt worked. Score it high.** `"open pr"` is a 5/5 when
  the context made it unambiguous. Do not deduct for missing file paths, missing success
  criteria, or brevity when the outcome shows none were needed. This is the single most
  common way prompt-scoring tools are wrong, and the reason this skill exists.
- **Friction → find the one token that would have prevented it.** Not a generic lecture.
  If the agent asked "which module?", the missing token was the module name. Name it.
- **Many turns, no friction → look for waste**, e.g. the agent explored to find something the
  user already knew and could have stated.
- **A correction is not always the prompt's fault.** `"you are wrong, the orders table exists"`
  is the agent being wrong, not the prompt being unclear. Say so — crediting the user is as
  important as coaching them, and a tool that blames them for every correction gets ignored.
- Weigh a repeated small leak over a spectacular one-off. Habits are what change.

Score each reviewed prompt **1-5** and always attach the evidence: *"3/5 — I asked which of
the 5 modules; `:api-module:` would have saved a turn."*

## Cold vs warm: where context actually pays

`--correlate` splits friction by prompt length, **cold (`seq` 1, first prompt of a session)
vs warm (`seq` 2+)**. The two curves typically run opposite ways, and that split is the
entire answer to "should I give more context?":

- **Cold**: friction falls as the opener gets longer. The session has no context yet, so the
  prompt must carry it. Cold prompts also tend to run several times more turns than warm
  ones — they launch the big work, so they are where the leverage is.
- **Warm**: friction *rises* with length — but that is a symptom, not a cause. A long warm
  prompt is long because the user is repairing or redirecting something that already went
  wrong. **Never tell them to write shorter warm prompts.** Read it as a signal the session
  has drifted and a fresh session with a good opener would be cheaper.

Report both curves with their n's, and say plainly which buckets are too small to lean on.

**What belongs in a cold opener** — derive these from their own corrections, don't recite
them. The four that usually earn their characters:

1. **Which repo or module**, when they work across many.
2. **Starting state** — `"on latest develop, ..."`. Catches the whole class of
   `"wrong, update from master"` corrections.
3. **The target system**, whenever something gets written or deleted.
4. **The constraint or prohibition** — `"do not push without my approval"`.

A prompt that outsources context to a link (a ticket, a PR) inherits whatever the link does
*not* say. Check the friction rate of URL-bearing prompts and, if it is high, tell them to
paste the link plus the one decision the ticket does not contain.

## Rewrites — the centre of the report

The rewrite is what teaches. A score tells them a prompt was weak; the rewrite shows them
what to type instead. Produce **5-8**, drawn from real friction prompts, and cover more than
one leak so the set is not repetitive.

Format each one exactly like this, so the delta is visible at a glance:

> **`"address pr comments; rebase"`** — 3 turns, you interrupted, then re-sent `"rebase"`
> **→** `"address pr comments"`, then `"rebase"` as a separate prompt
> **Changed:** split the bundle. **Prevents:** the second request being dropped.

Rules that keep rewrites usable:

- **Change one thing and name it.** A rewrite that fixes three things teaches nothing,
  because they cannot tell which change mattered.
- **Keep their voice.** Their lowercase, their abbreviations, their terseness. Never
  translate into prompt-engineering register — no "Please analyze the following", no
  "Context:/Task:/Constraints:" scaffolding. A rewrite they would not plausibly type is a
  failed rewrite even when it is technically better.
- **Respect the budget.** A warm rewrite may grow by roughly half. A cold opener may double.
  Beyond that, the fix is not a longer prompt — it is a different first move.
- **Quote the evidence** next to each: the turn count, the interrupt, or the exact
  `next_prompt` that shows what went wrong. The rewrite must be visibly answering something.
- **Include one rewrite of a prompt that already worked but ran expensively** (many turns, no
  friction). Those teach efficiency rather than repair, and they show the review is not
  only fault-finding.
- **Never invent a prompt.** Every "before" is a verbatim string from the data.

## Report

Keep it short and specific to their actual text. Structure:

1. **Score** — the period score, the distribution, and the cold/warm split, with the model's
   limits stated plainly. Then friction rate, median length, prompts reviewed.
2. **What's working** — the prompt shapes that consistently land, quoted. People need to know
   what to keep, not only what to fix.
3. **Cold vs warm** — both curves from `--correlate`, and what it means for their openers.
4. **Top 3 leaks, ranked by how often they cost a turn.** Each one: the pattern, two or three
   real quoted prompts, and the cost in turns.
5. **Best and worst, scored** — their top prompts with the work each delivered, and every
   bottom entry with its score, the evidence, and which ones are not their fault.
6. **Rewrites** — 5-8 before/after pairs in the format above. This is the section they will
   actually reread; give it the most room.
7. **One change to make this week.** One. Ranked by frequency × cost.

Quote real prompts throughout — generic advice about prompt engineering is worthless here and
they can read it anywhere. The value is that these are their own words.

## Notes

- Prompts are the user's own; nothing leaves the machine and no API is called beyond this session.
- `--min-turns 1` is the default and drops in-flight prompts from live sessions; pass `--min-turns 0` to see them.
- Pasted blocks are collapsed to `[pasted N chars]`. Pasting an error or a stack trace is *good*
  prompting — count it in their favour, never as noise.
- Prompts are truncated at `--max-chars` (default 1200); `truncated: true` flags it.
