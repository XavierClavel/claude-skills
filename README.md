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

It reads `~/.claude/projects/*/*.jsonl` locally. Nothing is uploaded and no API is called.

```bash
python3 scripts/extract.py --days 30 --score          # period score, best and worst prompts
python3 scripts/extract.py --days 30 --correlate      # where extra context actually pays
python3 scripts/extract.py --days 30 --friction-only  # the prompts worth rewriting
```

The score is `10 × 0.5^(waste/12)`, where waste counts only work that had to be redone.
Length and turn count are never penalised: a 442-turn prompt with no repair is a 10.

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
git commit -am "..."                      # the install pins a commit, so commit first
claude plugin marketplace update xavier
claude plugin update prompt-review        # restart Claude Code to apply
```

Check manifests before pushing:

```bash
claude plugin validate .
```
