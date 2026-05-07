#!/usr/bin/env python3
"""Idempotent COSMIC custom-shortcut writer.

Used by ``install-shortcut.sh`` to add (or replace) a single binding in
``~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom`` without
clobbering unrelated entries the user already has.

We don't depend on a RON parser — the COSMIC custom file has a stable,
line-oriented shape (one ``( … ): Spawn("…"),`` block per entry), so a small
regex pass is enough.

Dedup rules — we DROP an existing entry when:
  * its ``(modifiers, key)`` equals the new binding (rebinding the same key), OR
  * its ``Spawn(...)`` target is the new wrapper path or matches a known
    legacy basename (e.g. ``voicecli-dictate-nats``) — keeps re-runs and
    upgrades clean.

Usage:
    _cosmic_bind.py CUSTOM_PATH 'Ctrl,Shift' SPACE WRAPPER_PATH 'voiceCLI dictate' \\
                    [LEGACY_BASENAME ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENTRY_RE = re.compile(
    r"    \(\s*\n"
    r"        modifiers:\s*\[(?P<mods>[^\]]*)\],?\s*\n"
    r'        key:\s*"(?P<key>[^"]+)",?\s*\n'
    r"        description:\s*[^\n]*\n"
    r'    \):\s*Spawn\("(?P<spawn>[^"]+)"\),?\s*\n',
    re.DOTALL,
)
MOD_RE = re.compile(r"\b(Ctrl|Shift|Alt|Super)\b")


def main() -> int:
    if len(sys.argv) < 6:
        print(__doc__, file=sys.stderr)
        return 2
    custom_path = Path(sys.argv[1])
    new_mods = {m.strip() for m in sys.argv[2].split(",") if m.strip()}
    new_key = sys.argv[3]
    wrapper = sys.argv[4]
    description = sys.argv[5]
    legacy_basenames = set(sys.argv[6:])

    content = custom_path.read_text() if custom_path.exists() else "{\n}\n"

    kept: list[str] = []
    for m in ENTRY_RE.finditer(content):
        mods = set(MOD_RE.findall(m.group("mods")))
        key = m.group("key")
        spawn = m.group("spawn")
        spawn_base = Path(spawn).name
        same_binding = mods == new_mods and key == new_key
        same_target = spawn == wrapper
        legacy_target = spawn_base in legacy_basenames
        if same_binding or same_target or legacy_target:
            continue
        kept.append(m.group(0))

    new_entry = (
        "    (\n"
        "        modifiers: [\n"
        + "".join(f"            {mod},\n" for mod in sorted(new_mods))
        + "        ],\n"
        + f'        key: "{new_key}",\n'
        + f'        description: Some("{description}"),\n'
        + f'    ): Spawn("{wrapper}"),\n'
    )

    custom_path.parent.mkdir(parents=True, exist_ok=True)
    custom_path.write_text("{\n" + "".join(kept) + new_entry + "}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
