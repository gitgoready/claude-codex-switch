# Product Hunt Launch Draft

This is a draft for the Product Hunt launch of **Claude-Codex Switch**.
Refine the copy, prepare the gallery images, then submit at
https://www.producthunt.com/posts/new

---

## Product name
Claude-Codex Switch

## Tagline (max 60 chars)

Pick one:

1. **Switch Claude and Codex sessions when quota runs out** (52 chars) - RECOMMENDED, benefit-first
2. Migrate AI coding sessions between Claude and Codex (52 chars)
3. Never lose AI coding context when switching agents (49 chars)
4. One command to switch Claude Code <-> Codex (43 chars)

## Topics (pick up to 3)

- Developer Tools
- Artificial Intelligence
- Productivity

## Short description (max 260 chars)

Convert in-progress AI coding sessions between Claude Code and Codex in both directions. Switch agents when one quota runs out - without losing context. Cross-platform, one command, smart "From Claude/Codex" titles with timestamps.

(~248 chars)

## Longer description (for the body)

Most developers using AI coding agents hit the same wall: you're deep in a
complex task, your Claude Code or Codex quota runs out, and you have to
restart the conversation from scratch on the other tool.

**Claude-Codex Switch** solves this. It converts your session history between
Claude Code and Codex in both directions, so you can pick up exactly where
you left off - on whichever agent still has quota.

**One command, both directions:**

```bash
python scripts/converter.py claude-to-codex convert <session.jsonl>
python scripts/converter.py codex-to-claude convert <rollout.jsonl>
```

**What it does:**
- Converts Claude session JSONL into a Codex rollout JSONL + registers it in
  Codex's `state_5.sqlite` so Codex's UI lists it immediately.
- Converts Codex rollout JSONL into a Claude-compatible JSONL under
  `~/.claude/projects/<slug>/`, with a `migration_boundary` system entry
  marking the origin.
- Preserves tool calls, tool results, and compacted history (truncated for
  readability).
- Tags every migrated session with a title like
  `From Claude - <original session name> - 2026-07-17 14:30:05` so you can
  tell at a glance where it came from.

**Cross-platform:** Windows, Linux, macOS. Pure Python 3.7+, no shell
scripts, no dependencies beyond the standard library. UTF-8 safe on Windows
cmd.

**Also works as a Claude Code Skill** - drop the folder into
`~/.claude/skills/` and ask Claude "convert my current session to Codex" in
any future session.

Open source (MIT). Free forever. Star it if it saves your session.

## Maker comment (first comment, posted by you on launch day)

---

Hey Product Hunt! 👋

I built **Claude-Codex Switch** because I kept hitting the same wall: I'd be
30 minutes deep in a Codex session, the quota would run out, and I'd have to
restart the whole conversation in Claude Code (or vice versa). The context
was right there on disk - Codex stores sessions as JSONL, Claude stores
sessions as JSONL - but the two formats are completely incompatible.

So I wrote a converter. Then I cleaned it up, made it cross-platform, and
added a "From Claude/Codex" title prefix with a timestamp so I could tell
which sessions were migrated and when. Then I made it a Claude Code Skill so
I could just say "convert my session to Codex" instead of remembering CLI
flags.

**What makes it different from copy-pasting your last message:**
- Preserves the full conversation, including tool calls and tool results
  (not just the text).
- Registers the converted session in Codex's `threads` database, so Codex's
  history UI picks it up immediately - no scanning, no manual import.
- Injects a `migration_boundary` system entry on the Claude side so the
  transcript clearly shows where the session came from.
- Works on Windows, Linux, and macOS with zero non-stdlib dependencies.

**The pain point it solves:** AI agent quotas are usage-capped, but your
work-in-progress shouldn't be hostage to which cap you hit first. This tool
gives you a one-command escape hatch.

**What's next:** I'm considering adding a TUI for browsing/converting
sessions without remembering paths, and a "watch" mode that auto-converts
new sessions. If you'd use either, please say so in the comments - it helps
me prioritize.

Open source, MIT, free forever. If it saves your session, a GitHub star
makes my day. 🌟

Happy to answer any questions about how the conversion works, the format
differences between Claude and Codex sessions, or why I made certain
truncation tradeoffs.

---

## Gallery images (you need to create these)

Product Hunt requires at least 1 gallery image; 5-6 is ideal. Recommended:

1. **Hero image (1280x640)** - Tool name "Claude-Codex Switch" + tagline +
   a subtle split visual (Claude logo on left, Codex logo on right, arrow
   between). Make sure to add " unofficial / not affiliated" disclaimer in
   small text since both names are trademarks.
2. **Screenshot: `status` command** showing detected paths on Windows.
3. **Screenshot: `claude-to-codex convert`** output with the "From Claude"
   title visible.
4. **Screenshot: Codex history list** showing a migrated session with the
   "From Claude - ..." title.
5. **GIF: full workflow** (30-60s) - also goes in the README `docs/demo.gif`.
6. **Architecture diagram** (optional) - simple flowchart showing
   Claude JSONL <-> converter <-> Codex rollout + DB.

Tools to create these:
- Screenshots: Windows Snipping Tool, macOS Cmd+Shift+4, or `sharex`
- Hero image: Figma, Canva, or Excalidraw
- GIF: ScreenToGif (Windows), Kap (macOS), or `asciinema` + `agg` for
  terminal recordings

## Launch checklist

- [ ] Refine tagline and description above
- [ ] Create 5-6 gallery images
- [ ] Record `docs/demo.gif` and update README
- [ ] Get the repo to a clean state (no pending TODOs)
- [ ] Soft-launch to a few friends for first-day upvotes (PH ranking heavily
      weights the first few hours)
- [ ] Schedule launch on Product Hunt (pick a Tue/Wed/Thu, avoid Mon/Fri)
- [ ] Post maker comment within the first hour of going live
- [ ] Share on Twitter/X, LinkedIn, r/ClaudeAI, Hacker News (Show HN) on the
      same day
- [ ] Respond to every comment within 24h

## Trademark note

Both "Claude" (Anthropic) and "Codex" (OpenAI) are registered trademarks.
This is an unofficial, community-built tool - not affiliated with or
endorsed by either company. The Product Hunt submission should include this
disclaimer in the description or as a small note on the hero image to avoid
takedown requests.
