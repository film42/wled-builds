"""CLI entry point for wled-build."""

import argparse
import re
from pathlib import Path

from .builder import build_version
from .manifest import load_manifest
from .upstream import get_latest_stable, get_quinled_releases, get_wled_releases, WLED_REPO, QUINLED_REPO

# Only build versions >= this. Older versions predate ESP32 WireGuard support.
DEFAULT_MIN_VERSION = "0.15.4"


def _parse_version_tuple(v: str) -> tuple:
    """Parse a version string into a comparable tuple.

    Handles formats like "0.15.4", "0.15.0-b2", "16.0.0-beta", "0.15.0-rc.1".
    Stable releases sort after pre-releases of the same base version.
    """
    # Split off pre-release suffix
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[.-](.+))?$", v)
    if not match:
        return (0,)
    base = tuple(int(x) for x in match.group(1).split("."))
    pre = match.group(2)
    # Stable (no suffix) sorts after any pre-release
    if pre is None:
        return (*base, 1, "")
    return (*base, 0, pre)


def _version_gte(version: str, min_version: str) -> bool:
    """Check if version >= min_version."""
    return _parse_version_tuple(version) >= _parse_version_tuple(min_version)


def cmd_check(args):
    """Check which upstream releases we haven't built yet."""
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    min_version = args.min_version

    # Build a set of (version, source) tuples we've already built
    built: set[tuple[str, str]] = set()
    for entry in manifest:
        built.add((entry["version"], entry["source"]))

    print("Fetching upstream releases...")
    wled_releases = get_wled_releases()
    quinled_releases = get_quinled_releases()

    wled_latest = get_latest_stable(WLED_REPO)
    quinled_latest = get_latest_stable(QUINLED_REPO)

    print(f"  WLED:    {len(wled_releases)} releases, latest stable: {wled_latest['version'] if wled_latest else '?'}")
    print(f"  QuinLED: {len(quinled_releases)} releases, latest stable: {quinled_latest['version'] if quinled_latest else '?'}")

    quinled_versions = {r["version"] for r in quinled_releases}

    missing: list[dict] = []
    for r in wled_releases:
        v = r["version"]
        is_pre = r["prerelease"]
        tag = "pre-release" if is_pre else "stable"

        if not _version_gte(v, min_version):
            continue

        if (v, "generic") not in built:
            missing.append({"version": v, "source": "generic", "tag": tag})

        if v in quinled_versions and (v, "quinled") not in built:
            missing.append({"version": v, "source": "quinled", "tag": tag})

    if not missing:
        print(f"\nAll upstream releases >= {min_version} have been built.")
        return

    print(f"\nMissing builds ({len(missing)}):")
    for m in sorted(missing, key=lambda x: _parse_version_tuple(x["version"])):
        latest_marker = ""
        if wled_latest and m["version"] == wled_latest["version"]:
            latest_marker = "  <-- latest stable"
        print(f"  {m['version']:>14s}  {m['source']:<10s}  ({m['tag']}){latest_marker}")


def cmd_build(args):
    """Build firmware for a specific WLED version."""
    output = Path(args.output)
    build_version(
        version=args.version,
        output_base=output,
        source=args.source,
    )


def cmd_build_new(args):
    """Build any upstream releases >= min-version that we haven't built yet."""
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    output = Path(args.output)
    min_version = args.min_version

    built: set[tuple[str, str]] = set()
    for entry in manifest:
        built.add((entry["version"], entry["source"]))

    print("Fetching upstream releases...")
    wled_releases = get_wled_releases()
    quinled_releases = get_quinled_releases()
    quinled_versions = {r["version"] for r in quinled_releases}

    wled_latest = get_latest_stable(WLED_REPO)
    print(f"  WLED latest stable: {wled_latest['version'] if wled_latest else '?'}")

    # Collect all (version, source) pairs we need to build
    to_build: list[tuple[str, str]] = []
    for r in wled_releases:
        v = r["version"]
        if not _version_gte(v, min_version):
            continue

        if (v, "generic") not in built:
            to_build.append((v, "generic"))
        if v in quinled_versions and (v, "quinled") not in built:
            to_build.append((v, "quinled"))

    # Sort by version so we build oldest first
    to_build.sort(key=lambda x: _parse_version_tuple(x[0]))

    if not to_build:
        print(f"\nNothing to build — all releases >= {min_version} are up to date.")
        return

    print(f"\nWill build {len(to_build)} target(s):")
    for v, source in to_build:
        print(f"  {v:>14s}  {source}")

    for v, source in to_build:
        print(f"\n{'#' * 60}")
        print(f"# Building {source} targets for {v}")
        print(f"{'#' * 60}")
        build_version(version=v, output_base=output, source=source)


def main():
    parser = argparse.ArgumentParser(
        prog="wled-build",
        description="Build WLED firmware with WireGuard support",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- check ---
    p_check = sub.add_parser(
        "check",
        help="Show upstream releases not yet in our manifest",
    )
    p_check.add_argument(
        "--manifest", default="manifest.json",
        help="Path to manifest.json (default: manifest.json)",
    )
    p_check.add_argument(
        "--min-version", default=DEFAULT_MIN_VERSION,
        help=f"Only show versions >= this (default: {DEFAULT_MIN_VERSION})",
    )

    # --- build ---
    p_build = sub.add_parser(
        "build",
        help="Build firmware for a specific WLED version",
    )
    p_build.add_argument(
        "version",
        help="WLED version to build (e.g. 0.15.4)",
    )
    p_build.add_argument(
        "--source",
        choices=["generic", "quinled", "all"],
        default="all",
        help="Which target sets to build (default: all)",
    )
    p_build.add_argument(
        "--output", default="build_output",
        help="Output directory (default: build_output)",
    )

    # --- build-new ---
    p_new = sub.add_parser(
        "build-new",
        help="Build any upstream releases we haven't built yet (stable + beta)",
    )
    p_new.add_argument(
        "--manifest", default="manifest.json",
        help="Path to manifest.json (default: manifest.json)",
    )
    p_new.add_argument(
        "--output", default="build_output",
        help="Output directory (default: build_output)",
    )
    p_new.add_argument(
        "--min-version", default=DEFAULT_MIN_VERSION,
        help=f"Only build versions >= this (default: {DEFAULT_MIN_VERSION})",
    )

    args = parser.parse_args()

    commands = {
        "check": cmd_check,
        "build": cmd_build,
        "build-new": cmd_build_new,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
