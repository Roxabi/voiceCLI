---
name: voice-design
description: 'Design and optimize a TTS voice personality through evolutionary optimization with Telegram feedback. Fully autonomous — sends audio to Telegram, polls for user replies, runs multiple cycles until convergence. Triggers: "design a voice" | "voice design" | "tune voice personality" | "voice personality" | "/voice-design".'
version: 1.0.0
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion
---

# Voice Design Skill

Design a TTS voice personality through autonomous evolutionary optimization.
Send audio variants to Telegram, collect ranking feedback, iterate until convergence,
then write the winning profile to `voicecli.toml`.

---

## State variables (track throughout the session)

| Variable | Type | Description |
|---|---|---|
| `last_update_id` | int | Prevents re-reading old Telegram messages. Start at 0. |
| `current_winner` | dict | Winning attrs from last cycle: `accent`, `personality`, `speed`, `emotion`. |
| `runner_up` | dict | Second-best attrs from last cycle. |
| `cycle_winners` | list[str] | Position label of winner per convergence cycle ("a"/"b"/"c"). For plateau detection. |
| `VOICECLI_DIR` | str | `/home/mickael/projects/voiceCLI` — prefix all commands with `cd $VOICECLI_DIR &&`. |

---

## Phase 0 — Setup

1. Verify `tg.py` works:
   ```bash
   cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg message "Voice design session starting..."
   ```
   If this fails, stop and report the error to the user. Do not proceed until Telegram is reachable.

2. Read current `voicecli.toml` defaults for reference (so you know the baseline voice).

---

## Phase 1 — Brief

Use `AskUserQuestion` to gather the following (one question, present as a form with 4 fields):

```
- Language: (default: French)
- Use case: (e.g. "AI assistant", "podcast host", "audiobook narrator")
- Vibe keywords: 3–5 adjectives that describe the ideal voice (e.g. "warm, precise, calm")
- Anti-keywords: adjectives to avoid (e.g. "robotic, cold, monotone")
```

Store as: `LANGUAGE`, `USE_CASE`, `VIBES` (list), `ANTI_VIBES` (list).

After the user answers, send a Telegram message summarizing the brief:
```
Voice design brief:
- Language: {LANGUAGE}
- Use case: {USE_CASE}
- Vibes: {VIBES joined with ", "}
- Avoid: {ANTI_VIBES joined with ", "}

Starting exploration — 2 rounds of 5 profiles. Stand by...
```

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
   cd /home/mickael/projects/voiceCLI && uv run voicecli generate /tmp/vd_e{E}_{N}.md --mp3
   ```
   The output MP3 is at `/home/mickael/projects/voiceCLI/TTS/voices_out/vd_e{E}_{N}.mp3`.

3. Send to Telegram:
   ```bash
   cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg send \
     TTS/voices_out/vd_e{E}_{N}.mp3 \
     "[Explore {E}/2] P{N} — {one-line vibe summary}"
   ```
   The one-line vibe summary is a 5–8 word distillation of the profile's character.

After sending all 5 for the cycle:

```bash
cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg message \
  "Cycle {E}/2 done. Reply with ranking best→worst (e.g. 3>1>5>2>4)"
```

Then poll for the user's reply:
```bash
cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg poll 300 {last_update_id}
```

Parse the response:
```bash
cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg parse "{reply_text}"
```

From the parsed list:
- `current_winner` = profile at position [0]
- `runner_up` = profile at position [1]
- Update `last_update_id` from the poll result.

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
   cd /home/mickael/projects/voiceCLI && uv run voicecli generate /tmp/vd_c{C}_{V}.md --mp3
   ```
   Output: `TTS/voices_out/vd_c{C}_{V}.mp3`

3. Send to Telegram:
   ```bash
   cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg send \
     TTS/voices_out/vd_c{C}_{V}.mp3 \
     "[Converge {C}] V{V-label} — {one-line vibe}"
   ```

After sending all 3:
```bash
cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg message \
  "Round {C} done. Reply with winner (1/2/3 or a/b/c), or 'done' to finalize."
```

Poll reply:
```bash
cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg poll 300 {last_update_id}
```

Update `last_update_id`.

### Early stop

If the user replies with any of: `done`, `ok`, `save`, `finalize`, `stop` → skip remaining convergence cycles, go directly to Phase 4 with `current_winner`.

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

- **Same position wins three times in a row** (e.g. `["a", "a", "a"]`): Send Telegram message:
  ```
  The same variant keeps winning — we may have converged.
  Reply 'done' to finalize, or 'continue' for more cycles.
  ```
  Poll the reply. If "done" / "ok" / "finalize" → Phase 4. If "continue" → continue with bold mutations.

- **5 cycles completed without early stop** → go to Phase 4.

---

## Phase 4 — Finalize

1. Display the winning profile in a clear format using `AskUserQuestion`:

   ```
   Winning voice profile:

   accent:      {value}
   personality: {value}
   speed:       {value}
   emotion:     {value}

   Options:
   A) Save this profile to voicecli.toml
   B) Adjust one or more fields before saving
   C) Discard and keep current voicecli.toml
   ```

2. If B: ask which field(s) to adjust and take the new values. Re-display and confirm.

3. If A or after adjustment confirmed:

   Edit `voicecli.toml` `[defaults]` section. Update the four fields:
   - `accent`
   - `personality`
   - `speed`
   - `emotion`

   Use `Read` then `Edit` to make precise replacements. Do not touch any other field.

4. Send Telegram confirmation:
   ```bash
   cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg message \
     "Voice profile saved to voicecli.toml!"
   ```

5. Use `AskUserQuestion`:
   ```
   Profile saved. Would you like to commit the updated voicecli.toml?
   A) Yes — commit with message "chore(config): update voice personality via voice-design"
   B) No — leave uncommitted
   ```

   If A: run `git add voicecli.toml` then `git commit` with the message above plus the standard co-author footer.

---

## Error handling

- If `voicecli generate` fails for a profile: skip that profile, send Telegram "⚠️ Profile {N} failed to generate — skipping.", continue with the rest.
- If `tg poll` returns `{"text": null, ...}` (timeout): send Telegram "Still waiting for your reply..." and poll again once more. After two timeouts, use `AskUserQuestion` as fallback.
- If a curl / Telegram error occurs: report to user immediately via `AskUserQuestion` and pause.

---

## Important constraints

- All instruct attrs (`accent`, `personality`, `speed`, `emotion`) MUST be written in the target language. French voice → French attrs. English voice → English attrs.
- Never use `--engine` flag when generating — let `voicecli.toml` defaults pick the engine.
- Never modify voicecli.toml until Phase 4 is confirmed by the user.
- Always use absolute paths for file operations: `/home/mickael/projects/voiceCLI/...`.
- The `tg` commands must be run from the voiceCLI directory (`cd /home/mickael/projects/voiceCLI && uv run python -m voicecli.tg ...`).
