#!/usr/bin/env bash
# tools/check_quadlet_manifest_install.sh — bidirectional manifest↔install gate (voiceCLI).
#
# deploy/quadlet.toml is the authoritative component manifest. This gate asserts
# BOTH directions so a declared component is always installed AND no installed
# unit file is undeclared (the orphan-install class).
#
#   Forward:  every [component.*] container in quadlet.toml has its source file in
#             deploy/quadlet/ AND the install paths (Makefile quadlet-install +
#             deploy/install.sh) install via a deploy/quadlet/*.container glob, so
#             any declared unit's file is necessarily installed.
#   Reverse:  every deploy/quadlet/*.container file is declared in quadlet.toml.
#
# voiceCLI has no [volume.*]/[network.*]/[pod.*], no converge.sh, and no
# quadlet-install-verify UNITS array, so this gate is container-only — cf. the
# fuller roxabi-factory variant. The [secrets] block (env-file credential) is
# not a Quadlet unit and is ignored here.
#
# EXIT-CODE CONTRACT (roxabi container-deployment-standard): 0 = clean,
# 1 = violations found (merge-blocking), 2 = script setup error.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)" \
    || { echo "ERROR: not a git repository" >&2; exit 2; }

# Paths are env-overridable for testing; defaults are the repo-root locations.
QUADLET_TOML="${QUADLET_TOML:-deploy/quadlet.toml}"
QUADLET_DIR="${QUADLET_DIR:-deploy/quadlet}"
MAKEFILE="${MAKEFILE:-Makefile}"
INSTALL_SH="${INSTALL_SH:-deploy/install.sh}"

for req in "$QUADLET_TOML" "$MAKEFILE" "$INSTALL_SH"; do
    if [[ ! -f "$req" ]]; then
        echo "ERROR: required file not found: $req" >&2
        exit 2
    fi
done
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import tomllib' >/dev/null 2>&1; then
    echo "ERROR: python3 with tomllib (Python 3.11+) required" >&2
    exit 2
fi

fail=0

# Declared container basenames, one per line.
mapfile -t DECLARED < <(python3 - "$QUADLET_TOML" <<'PYEOF'
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
for comp in data.get("component", {}).values():
    container = comp.get("container")
    if container:
        print(container)
PYEOF
)

if [[ ${#DECLARED[@]} -eq 0 ]]; then
    echo "FAIL: no [component.*] containers parsed from $QUADLET_TOML — gate validated nothing" >&2
    exit 1
fi

# Install paths must use a dir-glob so any declared unit's file is installed.
if ! grep -qF 'deploy/quadlet/*.container' "$MAKEFILE"; then
    echo "FAIL: $MAKEFILE quadlet-install does not glob deploy/quadlet/*.container — install is not manifest-driven" >&2
    fail=1
fi
if ! grep -qF 'quadlet/*.container' "$INSTALL_SH"; then
    echo "FAIL: $INSTALL_SH does not glob quadlet/*.container — install is not manifest-driven" >&2
    fail=1
fi

# Forward: every declared container has its source file on disk (the glob installs it).
for container in "${DECLARED[@]}"; do
    if [[ ! -f "$QUADLET_DIR/$container" ]]; then
        echo "FAIL: $container declared in $QUADLET_TOML but $QUADLET_DIR/$container does not exist (glob cannot install it)" >&2
        echo "::error file=$QUADLET_TOML::declared container $container has no source file in $QUADLET_DIR/"
        fail=1
    fi
done

# Reverse: every on-disk *.container file is declared in the manifest.
declared_set=" ${DECLARED[*]} "
for fpath in "$QUADLET_DIR"/*.container; do
    [[ -e "$fpath" ]] || continue   # no-match glob guard
    fname="$(basename "$fpath")"
    if [[ "$declared_set" != *" $fname "* ]]; then
        echo "FAIL: $QUADLET_DIR/$fname is installed by the quadlet glob but NOT declared in $QUADLET_TOML (orphan install)" >&2
        echo "::error file=$QUADLET_DIR/$fname::orphan unit — declare it in $QUADLET_TOML [component.*] or remove the file"
        fail=1
    fi
done

if [[ "$fail" -ne 0 ]]; then
    echo "" >&2
    echo "Every [component.*] container in $QUADLET_TOML must have a source file installed by the" >&2
    echo "deploy/quadlet/*.container glob in $MAKEFILE + $INSTALL_SH, and every on-disk *.container" >&2
    echo "must be declared in the manifest (#1901)." >&2
    exit 1
fi

echo "quadlet manifest install check passed (bidirectional, container-only)"
exit 0
