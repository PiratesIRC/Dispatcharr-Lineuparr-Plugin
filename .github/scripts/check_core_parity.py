#!/usr/bin/env python3
"""Vendored-core drift gate (Layer A) - runs in CI, needs no workspace _shared/.

The committed vendored shared file (Lineuparr/matching_core.py) must hash-match the
sha256 pinned in core_manifest.json. This catches a hand-edit to the vendored copy that
silently diverges from the _shared source of truth. To land an INTENDED core change:
edit <workspace>/_shared/matching_core.py, re-run .github/scripts/sync_core.py (re-vendors
+ rewrites the manifest), regenerate the golden baseline if behavior changed, and commit
all of it together. See MATCHER-STANDARDIZATION-PLAN.md.

Exit 0 = match; exit 1 = drift/missing.
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent                  # <repo>/.github/scripts -> <repo>
INNER = next((p.parent for p in REPO_ROOT.glob("*/fuzzy_matcher.py")), REPO_ROOT / REPO_ROOT.name)
MANIFEST = HERE / "core_manifest.json"


def main() -> int:
    pins = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failed = False
    for fname, expected in sorted(pins.items()):
        path = INNER / fname
        if not path.exists():
            print(f"MISSING vendored {fname} (run .github/scripts/sync_core.py)")
            failed = True
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            print(f"DRIFT {fname}: {actual} != pinned {expected}")
            print("  If intended: edit _shared/, re-run sync_core.py, commit the new manifest.")
            failed = True
        else:
            print(f"OK {fname}: {actual[:16]}...")
    if failed:
        print("Core parity gate FAILED.")
        return 1
    print("Core parity gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
