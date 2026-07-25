#!/usr/bin/env python3
"""Run pyright over a package and compare the result against the checked-in baseline.

The repo carries a known, non-zero pyright error count (almost entirely in test
files, where mocks and fixtures are deliberately loosely typed). Gating on "zero
errors" would mean either a huge unrelated refactor or nobody running pyright at
all, so instead this enforces "no NEW errors": a file may not exceed its entry in
`tools/pyright_baseline.json`, and a file with no entry may not report any error.

Fixing errors is always allowed — the script reports the improvement and asks you
to lower the baseline in the same commit, so the number can only ratchet down.

Usage:
    uv run python ../../tools/check_pyright.py                 # infer package from cwd
    uv run python tools/check_pyright.py packages/cookbot-core # explicit
    uv run python tools/check_pyright.py --all                 # every package (needs each venv)

Exit code 0 = at or below baseline, 1 = regression or a stale baseline.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "tools" / "pyright_baseline.json"


def load_baseline() -> dict[str, dict[str, int]]:
    """Read the baseline, dropping `//`-prefixed keys (per-entry explanatory notes)."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {
        package: {path: count for path, count in entries.items() if not path.startswith("//")}
        for package, entries in data["packages"].items()
    }


def run_pyright(package_dir: Path) -> Counter[str]:
    """Run pyright in package_dir and return {relative posix path: error count}."""
    proc = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson"],
        cwd=package_dir,
        capture_output=True,
        text=True,
    )
    # pyright exits 1 when it reports errors, which is the normal case here. Only a
    # missing/broken tool (no parseable JSON on stdout) is a real failure.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"could not run pyright in {package_dir}:", file=sys.stderr)
        print(proc.stdout or proc.stderr, file=sys.stderr)
        raise SystemExit(2) from None

    counts: Counter[str] = Counter()
    for diag in report["generalDiagnostics"]:
        if diag.get("severity") != "error":
            continue
        rel = os.path.relpath(diag["file"], package_dir).replace("\\", "/")
        counts[rel] += 1
    return counts


def check_package(package: str, baseline: dict[str, int]) -> bool:
    package_dir = REPO_ROOT / package
    actual = run_pyright(package_dir)

    regressions: list[str] = []
    improvements: list[str] = []
    for path in sorted(set(actual) | set(baseline)):
        got, want = actual.get(path, 0), baseline.get(path, 0)
        if got > want:
            regressions.append(f"  {path}: {got} errors, baseline allows {want}")
        elif got < want:
            improvements.append(f"  {path}: {got} errors, baseline still says {want}")

    total, allowed = sum(actual.values()), sum(baseline.values())
    print(f"{package}: {total} errors (baseline {allowed})")

    if regressions:
        print("  NEW pyright errors — fix them, do not raise the baseline:")
        print("\n".join(regressions))
        return False
    if improvements:
        print("  errors were fixed — lower these entries in tools/pyright_baseline.json:")
        print("\n".join(improvements))
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", nargs="?", help="repo-relative package dir; defaults to cwd")
    parser.add_argument("--all", action="store_true", help="check every package in the baseline")
    args = parser.parse_args()

    baseline = load_baseline()

    if args.all:
        packages = list(baseline)
    elif args.package:
        packages = [args.package.replace("\\", "/").rstrip("/")]
    else:
        rel = os.path.relpath(Path.cwd(), REPO_ROOT).replace("\\", "/")
        packages = [rel]

    ok = True
    for package in packages:
        if package not in baseline:
            print(f"{package}: not in tools/pyright_baseline.json — add it or pass a package dir")
            return 1
        ok &= check_package(package, baseline[package])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
