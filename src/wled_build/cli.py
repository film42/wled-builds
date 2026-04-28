"""CLI entry point for wled-build."""

import argparse
import re
from pathlib import Path

from .builder import build_version
from .upstream import get_latest_stable, get_quinled_releases, get_wled_releases, WLED_REPO, QUINLED_REPO

# Only build versions >= this. Older versions predate ESP32 WireGuard support.
DEFAULT_MIN_VERSION = "0.15.4"


def _parse_version_tuple(v: str) -> tuple:
    """Parse a version string into a comparable tuple.

    Handles formats like "0.15.4", "0.15.0-b2", "16.0.0-beta", "0.15.0-rc.1".
    Stable releases sort after pre-releases of the same base version.
    """
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[.-](.+))?$", v)
    if not match:
        return (0,)
    base = tuple(int(x) for x in match.group(1).split("."))
    pre = match.group(2)
    if pre is None:
        return (*base, 1, "")
    return (*base, 0, pre)


def _version_gte(version: str, min_version: str) -> bool:
    """Check if version >= min_version."""
    return _parse_version_tuple(version) >= _parse_version_tuple(min_version)


def cmd_check(args):
    """Check which upstream releases we haven't built yet.

    Uses GitHub Release existence as the source of truth for what's been built,
    not a local manifest file.
    """
    from .publish import get_or_create_release, get_existing_assets
    min_version = args.min_version

    print("Fetching upstream releases...")
    wled_releases = get_wled_releases()
    quinled_releases = get_quinled_releases()

    wled_latest = get_latest_stable(WLED_REPO)
    quinled_latest = get_latest_stable(QUINLED_REPO)

    print(f"  WLED:    {len(wled_releases)} releases, latest stable: {wled_latest['version'] if wled_latest else '?'}")
    print(f"  QuinLED: {len(quinled_releases)} releases, latest stable: {quinled_latest['version'] if quinled_latest else '?'}")

    quinled_versions = {r["version"] for r in quinled_releases}

    versions_to_check: list[dict] = []
    for r in wled_releases:
        v = r["version"]
        if not _version_gte(v, min_version):
            continue
        has_quinled = v in quinled_versions
        tag = "pre-release" if r["prerelease"] else "stable"
        versions_to_check.append({"version": v, "tag": tag, "has_quinled": has_quinled})

    if not versions_to_check:
        print(f"\nNo upstream releases >= {min_version}.")
        return

    missing: list[dict] = []
    for vc in versions_to_check:
        v = vc["version"]
        # A version needs building if it has no release at all on our repo,
        # or if it's missing assets. For the check command, just see if the
        # release tag exists.
        from .publish import GITHUB_API, REPO, _session
        resp = _session().get(
            f"{GITHUB_API}/repos/{REPO}/releases/tags/v{v}",
            timeout=30,
        )
        if resp.status_code == 200:
            assets = {a["name"] for a in resp.json().get("assets", [])}
            has_generic = any(a.startswith("generic_") for a in assets)
            has_quinled_assets = any(a.startswith("quinled_") for a in assets)
        else:
            has_generic = False
            has_quinled_assets = False

        if not has_generic:
            missing.append({"version": v, "source": "generic", "tag": vc["tag"]})
        if vc["has_quinled"] and not has_quinled_assets:
            missing.append({"version": v, "source": "quinled", "tag": vc["tag"]})

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
    output = Path(args.output)
    min_version = args.min_version

    print("Fetching upstream releases...")
    wled_releases = get_wled_releases()
    quinled_releases = get_quinled_releases()
    quinled_versions = {r["version"] for r in quinled_releases}

    wled_latest = get_latest_stable(WLED_REPO)
    print(f"  WLED latest stable: {wled_latest['version'] if wled_latest else '?'}")

    # Build each version — the builder itself checks release assets to skip
    # envs that are already published, so we just need to decide which
    # (version, source) pairs to attempt.
    to_build: list[tuple[str, str]] = []
    for r in wled_releases:
        v = r["version"]
        if not _version_gte(v, min_version):
            continue
        to_build.append((v, "generic"))
        if v in quinled_versions:
            to_build.append((v, "quinled"))

    to_build.sort(key=lambda x: _parse_version_tuple(x[0]))

    if not to_build:
        print(f"\nNo releases >= {min_version} found.")
        return

    print(f"\nWill attempt {len(to_build)} build(s) (already-published envs will be skipped):")
    for v, source in to_build:
        print(f"  {v:>14s}  {source}")

    for v, source in to_build:
        print(f"\n{'#' * 60}")
        print(f"# {source} targets for {v}")
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
