---
name: prompt-review
description: Grade the user's own recent Claude Code prompts, show rewritten before/after versions of the ones that caused friction, and coach them on what to change — judged by what actually happened after each prompt (clarifying questions, corrections, interrupts, turns-to-done) rather than a generic checklist — then track their prompting week by week on a dashboard. Use when the user asks to review or rate their prompts, wants better prompt versions, asks how they prompt, wants to improve at prompting, asks whether to give more context, asks how their prompting has evolved, or invokes /prompt-review.
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
python3 scripts/extract.py --days 30 --shortcuts --top 10  # 4. searches a prompt could have cut short
python3 scripts/extract.py --days 14 --limit 50        # full slice, only if surveying
python3 scripts/extract.py --days 30 --project myrepo  # one repo
python3 scripts/extract.py --days 60 --timeline        # one JSON row per week
```

**Run 1, 2, 3 and 4 in that order, every time.** They are cheap and they set up the whole report;
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
| `next_is_go_ahead` | The next prompt only lets it carry on (`yes`, `go`, `open pr`). After `ended_on_a_question`, the prompt could have granted that up front |
| `assistant_steps` / `tool_calls` | Cost of the prompt. **A step is one assistant content block** (thinking, text, or one tool call) — a reply is ~1.5-2.6 of them |
| `assistant_replies` | Actual assistant messages. Use this when talking to the user about "turns"; use steps for the scoring maths |
| `seq` | Position in session. `1` is a cold start and is judged differently — see below |
| `seconds_to_next_prompt` | Long gap + no correction usually means it just worked |
| `calls_before_edit` / `searches_before_edit` | Tool calls (and how many were searches) before the first edit to the user's files. `null` when the prompt led to no edit |
| `other_repos_before_edit` | Repositories the agent reached before that edit, other than the one it works in |
| `notes_before_edit` | What the agent said it was looking for meanwhile: **read these to judge a shortcut** |

## The score

Every record carries a `score` from 0 to 10, and `--score` prints the period figure, the
distribution, and the best and worst prompts. The model is in `score()` in `extract.py`:

```
waste (in STEPS, not replies)                ROUND_TRIP  = 12  a human round trip
  early clarification (step k <= 5)  k + 12  HALFLIFE    = 12  waste that halves the score
  interrupt                          12 each, max 2      CORRECTION_CAP = 24
  next prompt corrects it            min(steps, 24)
  handed back on a question          12
score = 10 * 0.5 ** (waste / 12)
```

**Every time a prompt makes the user come back, its score halves:** one round trip is a 5, two
are a 2.5. A round trip used to cost 3 steps and score 8.4. That let a month in which one prompt
in five needed the user again average 9.6, which read as no room left to improve. Its price is
the user's attention, roughly a warm prompt's worth of agent work (median ~15 steps), so it is
set at one halflife.

The properties that make it defensible, and you should say so when reporting it:

- **Waste is absolute, not a share of the window.** A prompt that sends the agent 40 steps
  down the wrong path scores worse than one caught after 3. Normalising by steps would have
  rewarded the expensive mistakes.
- **Length and step count are never penalised.** A 442-step prompt with no repair is a 10.
  Big clean runs are what good prompts look like.
- **Report counts to the user in replies, not steps**, or say which unit you mean. 442 steps
  is ~250 replies, and "442 turns" overstates it by about double.
- **An interrupt is charged as one redirect, not as its whole prefix.** How much an interrupt
  threw away is not knowable — the agent may have gone wrong on its last step or its first.

**Limits to state in the report, not hide:**

- Most prompts still score 10 (typically ~80%): the score counts only waste it can detect, so it
  is a ceiling. Work the agent spent finding something the user already knew scores 10.
  Rank the "best" by clean work delivered, which is what `--score` does.
- A handback is charged even when the agent's question was a real one. Bare go-aheads
  (`next_is_go_ahead`) are the clearly avoidable share: say how many of the handbacks they are.
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

## Shortcuts: searching the prompt could have saved

Some clean prompts are still expensive. The agent spent dozens of calls finding something the
user already knew: which repo, the example to copy, the endpoint, how to run it. The score
can't see this, because nothing was redone. `--shortcuts` lists the candidates: prompts with 25+
calls before their first edit, or 10+ when the search reached another repository. It
can't say whether the user knew, so judge each candidate from its `notes_before_edit`:

- **A shortcut**: the agent was *locating* something the user very likely knew. Examples:
  "the other admin app has a Test Order page, likely the source to port"; "I need to confirm which
  backend this repo talks to"; resetting a worktree cut from a stale base, which
  `"rebase on remote."` prevents. Name the missing words.
- **Not a shortcut**: the agent was *understanding* or *investigating*: debugging a cause,
  reading code to design something new, learning a codebase's patterns, running and checking.
  That is the work the prompt asked for. Most candidates land here: in one 30-day sample,
  2 of the 10 heaviest were shortcuts.

Report the shortcuts as a leak like any other, with the call count as the cost, and give at
least one a rewrite: the opener plus the name of what was searched for, in their words
(`"…, like in the other admin app"`). When the same thing gets located in several sessions, the
fix belongs in that repo's CLAUDE.md, not in every prompt: say which repo and what line.
Shortcuts never touch the score or the dashboard. They are a judgment, and the dashboard
only charts what the transcripts measure.

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

1. **Score** — when the dashboard holds an earlier review, open with it in one line: the change
   it asked for, its tracked number before the review and in the complete weeks since, and the
   verdict, or that no complete week has passed yet (see "The evolution dashboard"). Then the
   period score beside the share of clean prompts (those that needed nothing more from the
   user, printed by `--score`), the distribution, and the cold/warm split, with the model's
   limits stated plainly. Then friction rate, median length, prompts reviewed.
2. **What you're doing better since tracking started** — a table built from the `progress`
   lines that `timeline.py` prints in step 3 of the dashboard section. The dashboard shows the
   same verdicts to whoever the user shares it with, so the two must agree:

   | What got better | Then | Now | Why it counts |
   |---|---|---|---|
   | Clean prompts | 73% | 81% | +8 pts on the first weeks tracked, 3.1 standard errors: not noise |

   **Only rows marked `better` go in.** Never promote a `within noise` row, never infer
   progress from a quote or a hunch, and never pad the table to fill it. When no row is
   `better`, write one line instead of the table, e.g. "Nothing is measurably better yet:
   clean prompts went 73% → 79%, but over too few weeks to tell from noise." Name the
   largest move and say it hasn't cleared the bar. Then add one line for any `worse` row.
   When a row carries the tag of the last review's change or of one of its leaks, say so:
   that is the change working.
3. **What's working** — the prompt shapes that consistently land, quoted. People need to know
   what to keep, not only what to fix.
4. **Cold vs warm** — both curves from `--correlate`, and what it means for their openers.
5. **Top 3 leaks, ranked by how often they cost a turn.** Each one: the pattern, two or three
   real quoted prompts, and the cost in turns. Shortcuts compete for a place like any leak.
6. **Best and worst, scored** — their top prompts with the work each delivered, and every
   bottom entry with its score, the evidence, and which ones are not their fault.
7. **Rewrites** — 5-8 before/after pairs in the format above. This is the section they will
   actually reread; give it the most room.
8. **Suggestions** — a table with one row per leak, ranked by frequency × cost. The first row
   is the one change for this week. Mark it, and keep it to one:

   | Suggestion | Fixes | Cost in the last 30 days | Tracked by |
   |---|---|---|---|
   | **This week:** end feature openers with `, open pr` | Finish line sent as a second prompt | 55 bare go-aheads | Bare go-aheads |
   | Name the example to copy: `like in the other admin app` | Searching for what you knew | 86 calls on one prompt | (judged, not tracked) |

   Write each suggestion as something they would type or do, in their voice. "Tracked by"
   names the dashboard number from step 4's `metric` list, or says the leak is judged and not
   tracked. These rows go onto the dashboard as its "Next suggestions" (step 4), so they must
   read without the rest of the report.
9. **The dashboard link**, last, in one line.

Quote real prompts throughout — generic advice about prompt engineering is worthless here and
they can read it anywhere. The value is that these are their own words.

## The evolution dashboard

Every review updates one Dashboard artifact titled **Prompting Evolution**, made for the user to
share. It shows the weekly score and share of clean prompts, what cost a round trip and cold vs
warm, then what is measurably better since tracking started, the latest review's suggestions,
and whether each review's change took. Its wording speaks of "the author", not "you",
because the person reading it may not be the user. It is also the skill's memory. Claude Code deletes transcripts
after `cleanupPeriodDays` (30 by default), so the stored weeks are the only record of anything
older. They are kept twice, on the dashboard and in `~/.claude/prompt-review/`.
`scripts/timeline.py` merges both with the weeks still on disk and keeps each week's fullest
copy, so losing either one loses nothing.

A week is stored as complete only if a run sees it while its transcripts are all on disk,
which means within about three weeks of its end. A longer gap leaves the weeks in it partial
or missing. When the last review is older than that, say so in the report and name the weeks.

`<skill>` is this skill's folder. `<run>` is a new folder in your scratchpad or temporary
directory, and every file of the run goes in it.

**Without the Artifact tools** (no `Artifact` or `ArtifactData`, even through ToolSearch), still
run `timeline.py --out <run>/out` after the report, with `--review` as in steps 4–5, so the
local copy keeps the weeks. Then say in one line that the dashboard catches up on the next run
that has the tools.

**Before the report:**

1. **Find it.** `Artifact` `list` with `type: "Dashboard"`, then pick the row marked `(mine)`
   and titled `Prompting Evolution`. Rows shared by others can carry the same title, so never
   take one of those. If there are several of yours, take the first listed and say so. If there
   is none, this is the first run: see "First run" below.
2. **Load its memory.** `ArtifactData` `list` on collection `datasets`. Keep the `version` of
   `weeks` and `reviews` and the asset id in each `source.url` (the 32 characters after
   `/_blob/`). Read each file with its own `Artifact` `read`, passing the dashboard `url`,
   `path` the asset id and `out_dir` `<run>/history` (`paths` doesn't take asset ids). It lands
   at `<run>/history/<id>.json`. `ArtifactData` `get` on collection
   `files`, doc `index.html`, with `out_dir` `<run>/history`, and keep its `version`.
3. **See where the last change stands:**

   ~~~bash
   python3 <skill>/scripts/timeline.py --out <run>/out --weeks <weeks file> \
     --reviews <reviews file> --stored-page <run>/history/files/index.html.json
   ~~~

   It prints the last four complete weeks and, for the last review, its change with its tracked
   number over the four complete weeks before the review and the complete weeks since, and a
   verdict, or `too soon` while no complete week has passed. That is the report's opening line.

   It also prints `progress since tracking started`: every tracked number over the first four
   complete weeks the history holds against the last four. Until there are eight, the older half
   against the newer half, so the two windows never overlap. Each row is `better`, `worse` or
   `within noise`, depending on whether the move clears 2.5 standard errors. Rows the last
   review tracked are tagged. This is the only source for "What you're doing better", in the
   report and on the dashboard.

**After the report:**

4. **Record the review** in `<run>/review.json`:

   ~~~json
   {"change": "<the one change, one sentence, as the report put it>",
    "metric": "<the weekly number that will show whether it took>",
    "suggestions": [
      {"text": "<this week's change, as the suggestions table put it>", "fixes": "<the leak, a short name>",
       "cost": "<its cost in the window, e.g. 55 bare go-aheads>", "metric": "<its number, or null>"},
      {"text": "<the next suggestion>", "fixes": "<its leak>", "cost": "<…>", "metric": null}]}
   ~~~

   `suggestions` is the report's suggestions table, row for row and in its order, this week's
   change first. The dashboard shows it as plain text: no markdown, and words to type go in
   double quotes. Give each the number that would fall if its leak were fixed, or `null` when
   none does (a shortcut, a judged leak). The next review's `progress` tags those numbers.
   `timeline.py` adds what is better since tracking started to the same row, so the dashboard
   shows the two together. Pick `metric` by
   what the change prevents. Use `go_ahead_rate` for a missing end state (the
   agent stops to ask what the prompt could have granted), `correction_rate` for a missing
   qualifier or target, `interrupt_rate` for scope that runs the wrong way, and `clarify_rate`
   for openers that get questioned at once. Use `friction_rate` when it spans these,
   `clean_share` when it should cut round trips of every kind, and `score` when nothing narrower
   fits. Leaks and suggestions are habits and the words to type, never quoted prompts: the file
   goes onto a dashboard the user may share.
5. **Build the files.** Run step 3's command again, adding `--review <run>/review.json --days 30`.
   It writes `weeks.json`, `reviews.json` and `page.json` to `<run>/out`, and ends with
   `page: write` or `page: keep`.
6. **Upload.** Make one `Artifact` call per file with the dashboard `url`, `asset: true` and
   `file_path` `weeks.json`, then the same for `reviews.json` (JSON files can't share a call).
   Keep each returned `/_blob/<id>` url.
7. **Point the dashboard at them.** Send one `ArtifactData` `batch`. It holds an `update` of
   `datasets/weeks` and of `datasets/reviews`, each
   `{"source": {"kind": "file", "url": "<new url>", "name": "<weeks|reviews>.json", "format": "json"}, "updated": {"at": "<now>", "by": "prompt-review skill"}}`,
   with `<now>` from `date -u +%Y-%m-%dT%H:%M:%SZ`, never guessed,
   and pinned with `if_version`. On `page: write`, it also holds a `set` of `files/index.html`
   with `file_path` `<run>/out/page.json` and that document's `if_version`. If the batch is
   refused because a document changed, read it again and redo that entry.
8. **Delete the replaced files.** Do this only after step 7 committed: `Artifact`
   `action: "delete"` with the dashboard `url` and `path` each old asset id from step 2.
9. **If the page was written, check it.** `ArtifactData` `get` on collection `data/users/me`,
   doc `check`. On `checking`, read again, at most five times. On `failed`, report what it
   names and don't patch the stored page: the fix belongs in `assets/dashboard.html`. No
   document after two reads means the dashboard isn't open, which is fine.

**First run**, when step 1 finds nothing. Do it after the report, in place of steps 2–8:

1. Call `Artifact` `quickstart` with `intent: "other"` and `design_systems: false` to get the
   Dashboard type's `type_url`. Publish with that `type_url`, `title: "Prompting Evolution"` and
   `auto_open: "after_first_write"`. The page comes from `assets/dashboard.html`, not from the
   type's instructions.
2. Write `<run>/review.json` as in step 4, then run `timeline.py --out <run>/out --review
   <run>/review.json --days 30` with no history flags.
3. Upload `weeks.json` and `reviews.json` as in step 6.
4. Send one `ArtifactData` `batch`. These writes create the documents, so they take no
   `if_version`:
   - `dash/meta`: `{"title": "Prompting Evolution"}`
   - `datasets/weeks`: `{"title": "Weekly prompting numbers", "description": "One row per week of Claude Code prompts: score, friction and its parts, bare go-aheads. Computed by the prompt-review skill from local transcripts; weeks whose transcripts are gone are kept from earlier runs.", "source": …, "updated": …}`
   - `datasets/reviews`: `{"title": "Prompt reviews", "description": "One row per prompt review: the score that day, the one change it asked for and whether it took, its suggestions, and what is measurably better since tracking started.", "source": …, "updated": …}`
   - `files/index.html` with `file_path` `<run>/out/page.json`
5. Check it as in step 9, then tell the user once that the dashboard is private until they share
   it, and that anyone they share it with can read its two files. The files hold weekly numbers
   and each review's change, suggestions and verdicts, never their prompts.

**Changing the merge or the verdicts.** `scripts/test_timeline.py` pins every way the history
could be lost or degraded, and when a move counts as better. Run it after touching
`merge_weeks`, `transcript_horizon`, `progress` or `followup`.

**Changing the page.** Its source is `assets/dashboard.html`. A run writes it over the stored
page only when the `prompt-review-page` number in the file is higher than the stored one, so
bump that number with every change. Edits made by hand on the dashboard survive until then.
The page follows the Dashboard type's one-writer rule: fixed text sits in the HTML, and
everything the data decides is a `dash.draw` drawing.

## Notes

- Prompts are the user's own and are read on this machine. The dashboard receives weekly
  numbers and each review's change, suggestions and verdicts, never prompt text.
- `--min-turns 1` is the default and drops in-flight prompts from live sessions; pass `--min-turns 0` to see them.
- Pasted blocks are collapsed to `[pasted N chars]`. Pasting an error or a stack trace is *good*
  prompting — count it in their favour, never as noise.
- Prompts are truncated at `--max-chars` (default 1200); `truncated: true` flags it.
