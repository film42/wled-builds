"""Audit logging for build provenance and supply chain transparency.

Every build produces a structured JSON record capturing the exact inputs,
outputs, and hashes needed to verify what was built and from what source.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class BuildRecord:
    version: str
    env_name: str
    source: str  # "generic" or "quinled"
    wled_commit: str
    quinled_commit: str | None
    wireguard: bool
    original_ini_sha256: str
    patched_ini_sha256: str
    binary_sha256: str | None
    binary_filename: str | None
    binary_size: int | None
    build_success: bool
    build_log: str
    built_at: str


def write_build_record(path: Path, records: list[BuildRecord]):
    """Write build records as JSON for audit trail."""
    data = [asdict(r) for r in records]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Audit log written: {path}")


def read_build_records(path: Path) -> list[dict]:
    """Read build records from a JSON audit file."""
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)
