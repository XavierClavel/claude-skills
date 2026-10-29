# claude-skills

My personal [Claude Code](https://claude.com/claude-code) skills, published as a plugin marketplace.

## Install

```bash
claude plugin marketplace add XavierClavel/claude-skills
claude plugin install prompt-review@xavier
```

Restart Claude Code, then run `/prompt-review`.

## Plugins

### `prompt-review`

Grades your own Claude Code prompts against **what actually happened after each one** —
clarifying questions, corrections, interrupts, turns-to-done — instead of against a generic
checklist. Checklist scorers mark down `"open pr"` for lacking a file path and success
criteria; this one reads the transcript, sees that it worked, and scores it a 10.

It reads `~/.claude/projects/*/*.jsonl` locally. Prompts never leave the machine.

```bash
python3 scripts/extract.py --days 30 --score          # period score, best and worst prompts
python3 scripts/extract.py --days 30 --correlate      # where extra context actually pays
python3 scripts/extract.py --days 30 --friction-only  # the prompts worth rewriting
python3 scripts/extract.py --days 30 --shortcuts      # clean prompts that searched for what you knew
python3 scripts/extract.py --days 60 --timeline       # one row of numbers per week
```

The report opens with **what you're doing better since tracking started**: a table of the numbers
whose move on the first weeks tracked clears 2.5 standard errors, and only those. It closes with
a table of **suggestions**, one per leak, the first being the one change to make this week.

Every review also updates a private **Prompting Evolution** dashboard, a claude.ai artifact made
from the Dashboard type and written to be shared. It charts the weekly score, the share of clean
prompts and what cost a round trip, then carries the same two tables, what is measurably better
since tracking started and the next suggestions, and whether each review's change took on the
weeks after it. Claude Code deletes transcripts after 30 days by default, so the history is kept
twice, on the dashboard and in `~/.claude/prompt-review/`. `scripts/timeline.py` merges both
with the weeks still on disk and keeps each week's fullest copy. Run a review at least every
three weeks: a week is only captured whole while all its transcripts are on disk. The dashboard
holds weekly numbers and each review's change, suggestions and verdicts, never prompt text. The
page source is `assets/dashboard.html`.

The score is `10 × 0.5^(waste/12)`, where waste counts only work that had to be redone and
each time a prompt made you come back costs a halving: one round trip is a 5, two are a 2.5.
Length and step count are never penalised: a 442-step prompt with no repair is a 10.
Its companion is the share of **clean prompts**, those that needed nothing more from you.
A *step* is one assistant content block (thinking, text, or a single tool call); an assistant
reply is roughly 1.5-2.6 of them.

## Adding a skill

1. `mkdir -p plugins/<plugin>/skills/<skill>` and write `SKILL.md` there.
2. Add `plugins/<plugin>/.claude-plugin/plugin.json` with `name` and `description`.
3. Add an entry to `plugins` in `.claude-plugin/marketplace.json`.
4. Commit and push. `claude plugin marketplace update xavier` picks it up.

Reference scripts as `"${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/skills/<skill>"` so they resolve
both when installed as a plugin and when dropped loose into `~/.claude/skills`.

## Local development

Point a marketplace at this working copy instead of GitHub:

```bash
claude plugin marketplace add ~/IdeaProjects/claude-skills
claude plugin install prompt-review@xavier
```

Install **copies** the plugin into `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`
and pins it to a commit SHA, so edits here are not live. After changing a skill:

```bash
# Bump "version" in plugins/<plugin>/.claude-plugin/plugin.json FIRST -- `plugin update`
# compares versions, so without a bump it reports "already at the latest" and does nothing.
git commit -am "..." && git push
claude plugin marketplace update xavier
claude plugin update prompt-review        # restart Claude Code to apply
```

Check manifests before pushing:

```bash
claude plugin validate .
```
