#!/usr/bin/env python3
"""Vendor the shared matching core into Lineuparr's inner folder (local dev tool).

The source of truth is <workspace>/_shared/matching_core.py. This re-vendors it into the
deployable inner folder and (re)writes .github/scripts/core_manifest.json with its sha256.
It self-locates assuming the layout:
    <workspace>/_shared/matching_core.py
    <workspace>/Lineuparr/.github/scripts/sync_core.py   (this file)
    <workspace>/Lineuparr/Lineuparr/                      (flat inner = deploy artifact)

Modes:
  (default)  write: copy _shared/matching_core.py into the inner folder and rewrite the
             manifest with its sha256 hash.
  --check    verify the vendored copy is byte-identical to _shared (the "forgot to sync"
             gate). Needs _shared present, so it runs LOCALLY only, never in GitHub CI.
  --dry-run  print what write would do without touching anything.

The per-plugin CI gate is SEPARATE and needs no _shared: .github/scripts/check_core_parity.py
asserts sha256(inner/matching_core.py) == core_manifest.json. Keep the vendored file OUT of
any bump_version lockstep stamping, or the hash gate fails forever.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

SHARED_FILES = ["matching_core.py"]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locate():
    """Return (inner_dir, shared_dir, manifest_path)."""
    here = Path(__file__).resolve()
    repo_root = here.parents[2]               # .github/scripts/sync_core.py -> <repo>/
    workspace = repo_root.parent              # <workspace>/
    shared_dir = workspace / "_shared"
    inner_dir = repo_root / repo_root.name    # <repo>/Lineuparr/  (flat inner folder)
    manifest_path = here.parent / "core_manifest.json"
    return inner_dir, shared_dir, manifest_path


def do_write(inner_dir: Path, shared_dir: Path, manifest_path: Path, dry_run: bool) -> int:
    manifest = {}
    for fname in SHARED_FILES:
        src = shared_dir / fname
        if not src.exists():
            print(f"ERROR: shared source missing: {src}")
            return 1
        digest = file_sha256(src)
        manifest[fname] = digest
        dst = inner_dir / fname
        if dry_run:
            print(f"  would copy {src} -> {dst}  ({digest[:12]}...)")
        else:
            shutil.copyfile(src, dst)
            print(f"  vendored {fname}  ({digest[:12]}...)")
    if dry_run:
        print(f"  would write manifest {manifest_path}")
        return 0
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"  wrote manifest {manifest_path}")
    return 0


def do_check(inner_dir: Path, shared_dir: Path) -> int:
    drift = 0
    for fname in SHARED_FILES:
        src, dst = shared_dir / fname, inner_dir / fname
        if not src.exists():
            print(f"ERROR: shared source missing: {src}")
            return 1
        if not dst.exists():
            print(f"DRIFT: vendored copy missing: {dst} (run sync_core.py)")
            drift += 1
            continue
        if file_sha256(src) != file_sha256(dst):
            print(f"DRIFT: {fname} vendored copy differs from _shared (run sync_core.py)")
            drift += 1
    if drift:
        print(f"sync check FAILED: {drift} file(s) out of sync")
        return 1
    print("sync check OK: vendored core matches _shared")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Vendor the shared matching core into Lineuparr.")
    ap.add_argument("--check", action="store_true", help="verify vendored == _shared, don't write")
    ap.add_argument("--dry-run", action="store_true", help="show what write would do")
    args = ap.parse_args()

    inner_dir, shared_dir, manifest_path = locate()
    if args.check:
        return do_check(inner_dir, shared_dir)
    return do_write(inner_dir, shared_dir, manifest_path, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
