---
name: voice-design
description: 'Design and optimize a TTS voice personality through evolutionary optimization. Generates batches of profile variants, waits for ranking feedback, iterates until convergence, then writes the winning profile to voicecli.toml. Triggers: "design a voice" | "voice design" | "tune voice personality" | "voice personality" | "/voice-design".'
version: 2.0.0
allowed-tools: Read, Edit, Write, Bash, Glob, Grep
---

# Voice Design Skill

Design a TTS voice personality through evolutionary optimization.
Generate batches of profile variants, wait for ranking feedback between batches, iterate until convergence, then write the winning profile to `~/.voicecli/voicecli.toml`.

Audio delivery (listening, sharing) is out of scope — the skill produces MP3 files on disk and returns their paths. Playback is the user's concern.

---

## State variables (track throughout the session)

| Variable | Type | Description |
|---|---|---|
| `current_winner` | dict | Winning attrs from last cycle: `accent`, `personality`, `speed`, `emotion`. |
| `runner_up` | dict | Second-best attrs from last cycle. |
| `cycle_winners` | list[str] | Position label of winner per convergence cycle ("a"/"b"/"c"). For plateau detection. |
| `VOICECLI` | str | Command to invoke voicecli (see Phase 0 auto-discovery). |
| `VOICES_OUT` | str | Output dir — default `~/.voicecli/TTS/voices_out` (expand `~` to absolute). |
| `CONFIG` | str | Config file — default `~/.voicecli/voicecli.toml` (expand `~` to absolute). |

---

## Phase 0 — Setup

Auto-discover `voicecli` and resolve paths:

```bash
# 1. voicecli command
if command -v voicecli &>/dev/null; then
  VOICECLI="voicecli"
else
  for d in . .. ../voiceCLI ~/projects/voiceCLI; do
    test -f "$d/src/voicecli/cli.py" && VOICECLI_DIR="$(cd "$d" && pwd)" && break
  done
  [ -z "$VOICECLI_DIR" ] && echo "ERROR: voicecli not found" && exit 1
  VOICECLI="cd $VOICECLI_DIR && uv run voicecli"
fi

# 2. Resolve paths (expand ~ explicitly)
VOICES_OUT="$HOME/.voicecli/TTS/voices_out"
CONFIG="$HOME/.voicecli/voicecli.toml"

# 3. Sanity checks
[ -f "$CONFIG" ] && echo "OK — config: $CONFIG" || echo "WARN — no $CONFIG (will be created on first save)"
mkdir -p "$VOICES_OUT"
```

Read `$CONFIG` (if present) to know the baseline voice.

---

## Phase 1 — Brief

Ask the user (plain text prompt, wait for reply):

```
Brief — please provide:
1. Language (default: French)
2. Use case (e.g. AI assistant, podcast host, audiobook narrator)
3. Vibe keywords — 3–5 adjectives (e.g. warm, precise, calm)
4. Anti-keywords — adjectives to avoid (e.g. robotic, cold, monotone)
```

Store as: `LANGUAGE`, `USE_CASE`, `VIBES` (list), `ANTI_VIBES` (list).

---

## Phase 2 — Exploration (2 cycles × 5 profiles)

### Standard French test phrase

> Bonjour ! Je suis votre assistante vocale. Je suis là pour vous aider avec vos questions techniques et vos projets. N'hésitez pas à me demander ce dont vous avez besoin, je ferai de mon mieux pour vous répondre.

For other languages, translate the above phrase into the target language before using it.

### Diversity matrix

Each cycle, generate exactly 5 profiles using this diversity matrix. Adapt wording to VIBES and the target language — do not use English in instruct fields when the target language is French (or any other non-English language).

| # | Formality | Energy | Warmth | Character |
|---|-----------|--------|--------|-----------|
| P1 | Formal | Medium | Cool | Neutral |
| P2 | Casual | High | Warm | Friendly |
| P3 | Semi-formal | Low | Warm | Wise / mentor |
| P4 | Casual | Very high | Neutral | Playful / mischievous |
| P5 | Semi-formal | Medium | Warm | Intellectual / geek |

Each profile has four attributes written **in the target language**:
- `accent`: pronunciation style / regional origin
- `personality`: character traits, relational manner
- `speed`: tempo, rhythm, breathing pattern
- `emotion`: emotional state or mood coloring

### Cycle flow

For each cycle E (1 then 2):

**Cycle 2 seeding**: Center P1 on cycle-1 winner. Blend winner + runner-up for P2. Use diversity matrix for P3–P5.

For each profile P1–P5:

1. Write `/tmp/vd_e{E}_{N}.md` with frontmatter + test phrase:
   ```markdown
   ---
   language: {LANGUAGE}
   accent: "{accent}"
   personality: "{personality}"
   speed: "{speed}"
   emotion: "{emotion}"
   segment_gap: 0
   crossfade: 0
   ---

   {test phrase in target language}
   ```

2. Generate audio:
   ```bash
   $VOICECLI generate /tmp/vd_e${E}_${N}.md --mp3
   ```
   Output: `$VOICES_OUT/vd_e{E}_{N}.mp3`

After generating all 5 for the cycle, list the outputs and wait for the user's ranking (plain text reply):

```
Cycle {E}/2 — 5 profiles generated:

P1 — {one-line vibe} → $VOICES_OUT/vd_e{E}_1.mp3
P2 — {one-line vibe} → $VOICES_OUT/vd_e{E}_2.mp3
P3 — {one-line vibe} → $VOICES_OUT/vd_e{E}_3.mp3
P4 — {one-line vibe} → $VOICES_OUT/vd_e{E}_4.mp3
P5 — {one-line vibe} → $VOICES_OUT/vd_e{E}_5.mp3

Reply with ranking best→worst (e.g. 3>1>5>2>4).
```

Parse the ranking (simple `>`-separated list, e.g. `3>1>5>2>4`):

From the parsed list:
- `current_winner` = profile at position [0]
- `runner_up` = profile at position [1]

After cycle 1, move to cycle 2 with winner/runner-up seeding.
After cycle 2, move to Phase 3.

---

## Phase 3 — Convergence (up to 5 cycles × 3 profiles)

### Mutation rules

Starting from `current_winner` attrs, generate 3 variants per cycle:

| Variant | Label | Rule |
|---------|-------|------|
| V1 | a | Amplify the dominant quality of winner — push the most prominent trait further |
| V2 | b | Blend winner + runner-up — take winner as base, inject runner-up's strongest single quality |
| V3 | c | Refine / polish — same essence as winner, sharper and more precise wording |

### Cycle flow

For each cycle C (1 → up to 5):

For each variant a/b/c:

1. Write `/tmp/vd_c{C}_{V}.md` (same structure as exploration).

2. Generate audio:
   ```bash
   $VOICECLI generate /tmp/vd_c${C}_${V}.md --mp3
   ```
   Output: `$VOICES_OUT/vd_c{C}_{V}.mp3`

After generating all 3, list outputs and wait for the user's pick (plain text):

```
Round {C} — 3 variants generated:

a — {one-line vibe} → $VOICES_OUT/vd_c{C}_a.mp3
b — {one-line vibe} → $VOICES_OUT/vd_c{C}_b.mp3
c — {one-line vibe} → $VOICES_OUT/vd_c{C}_c.mp3

Reply with winner (1/2/3 or a/b/c), or 'done' to finalize.
```

### Early stop

If the reply is any of: `done`, `ok`, `save`, `finalize`, `stop` → skip remaining convergence cycles, go directly to Phase 4 with `current_winner`.

### Update winner

Map reply to variant:
- "1" or "a" → V1 (a)
- "2" or "b" → V2 (b)
- "3" or "c" → V3 (c)

Set `current_winner` = chosen variant's attrs.
Set `runner_up` = second choice (or previous winner if user only gave one position).
Append winner label to `cycle_winners`.

### Plateau detection

After updating `cycle_winners`:

- **Same position wins twice in a row** (e.g. `["a", "a"]`): Apply bold mutations next cycle.
  Bold mutation means: exaggerate attrs beyond normal range, try a contrasting sub-style for V2, use very concrete sensory language for V3.

- **Same position wins three times in a row** (e.g. `["a", "a", "a"]`): Ask the user (plain text):
  ```
  The same variant keeps winning — we may have converged.
  Reply 'done' to finalize, or 'continue' for more cycles.
  ```
  If "done" / "ok" / "finalize" → Phase 4. If "continue" → continue with bold mutations.

- **5 cycles completed without early stop** → go to Phase 4.

---

## Phase 4 — Finalize

1. Display the winning profile as plain text and wait for reply:

   ```
   Winning voice profile:

   accent:      {value}
   personality: {value}
   speed:       {value}
   emotion:     {value}

   Reply:
   A) Save this profile to voicecli.toml
   B) Adjust one or more fields before saving
   C) Discard and keep current voicecli.toml
   ```

2. If B: ask which field(s) to adjust and take the new values (plain text). Re-display and confirm.

3. If A or after adjustment confirmed:

   Edit `$CONFIG` `[defaults]` section. Update the four fields:
   - `accent`
   - `personality`
   - `speed`
   - `emotion`

   If `$CONFIG` does not exist, copy `voicecli.example.toml` from the voicecli repo (or `$VOICECLI_DIR/voicecli.example.toml`) to `$CONFIG` first, then edit.

   Use `Read` then `Edit` to make precise replacements. Do not touch any other field.

4. Ask (plain text, wait for reply):
   ```
   Profile saved. Commit the updated voicecli.toml?
   A) Yes — commit with message "chore(config): update voice personality via voice-design"
   B) No — leave uncommitted
   ```

   Note: `$CONFIG` lives in `~/.voicecli/` (user config — typically not tracked by a repo). Default to B unless the user deliberately versions their voicecli config.

   If A: run `git add` on `$CONFIG` (from whichever repo tracks it) then `git commit` with the message above plus the standard co-author footer.

---

## Error handling

- If `voicecli generate` fails for a profile: skip that profile, report "⚠️ Profile {N} failed to generate — skipping.", continue with the rest.
- If the user's ranking reply is malformed: ask once for clarification, then proceed with best-effort parse.

---

## Important constraints

- All instruct attrs (`accent`, `personality`, `speed`, `emotion`) MUST be written in the target language. French voice → French attrs. English voice → English attrs.
- Never use `--engine` flag when generating — let `voicecli.toml` defaults pick the engine.
- Never modify `voicecli.toml` until Phase 4 is confirmed by the user.
- The skill produces MP3 files and file paths only — integrations (Telegram, gallery, browser, etc.) are out of scope.
