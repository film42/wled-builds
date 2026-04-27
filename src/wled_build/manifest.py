"""Manage the build manifest — the searchable index of all firmware builds.

The manifest is a flat JSON array designed to be fetched once by a frontend
and filtered client-side. Both "search by device" and "search by version"
are just filters on the same list.
"""

import json
from pathlib import Path

from .audit import BuildRecord


def load_manifest(path: Path) -> list[dict]:
    """Load the manifest from disk, returning an empty list if missing."""
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save_manifest(path: Path, entries: list[dict]):
    """Write the manifest to disk."""
    with open(path, "w") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")


def add_builds_to_manifest(
    manifest_path: Path,
    records: list[BuildRecord],
    base_url: str = "",
):
    """Add successful build records to the manifest, replacing any existing
    entries for the same (device, version, source) tuple."""
    manifest = load_manifest(manifest_path)

    for record in records:
        if not record.build_success:
            continue

        entry = {
            "device": record.env_name,
            "version": record.version,
            "source": record.source,
            "wireguard": record.wireguard,
            "wled_commit": record.wled_commit,
            "quinled_commit": record.quinled_commit,
            "filename": record.binary_filename,
            "sha256": record.binary_sha256,
            "size_bytes": record.binary_size,
            "built_at": record.built_at,
            "build_log": record.build_log,
        }
        if base_url:
            entry["url"] = f"{base_url}/{record.version}/{record.binary_filename}"

        # Remove any prior entry for the same device+version+source
        manifest = [
            m for m in manifest
            if not (
                m["device"] == entry["device"]
                and m["version"] == entry["version"]
                and m["source"] == entry["source"]
            )
        ]
        manifest.append(entry)

    manifest.sort(key=lambda m: (m["version"], m["source"], m["device"]))
    save_manifest(manifest_path, manifest)
    print(f"Manifest updated: {manifest_path} ({len(manifest)} entries)")
