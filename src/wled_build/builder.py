"""Clone WLED, apply WireGuard patches, build firmware, and collect artifacts.

Each build is fully isolated in a temp directory. The audit trail captures
original and patched INI files, build logs, and binary checksums.
"""

import hashlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .audit import BuildRecord, write_build_record
from .patcher import get_default_envs, patch_ini
from .upstream import (
    fetch_quinled_override,
    get_quinled_commit_sha,
    get_wled_commit_sha,
)

WLED_GIT_URL = "https://github.com/wled/WLED.git"


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_str(s: str) -> str:
    """Compute SHA-256 hex digest of a string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def clone_wled(version: str, parent_dir: Path) -> Path:
    """Shallow-clone WLED at a specific version tag."""
    wled_dir = parent_dir / "WLED"
    parent_dir.mkdir(parents=True, exist_ok=True)
    print(f"Cloning WLED v{version} into {wled_dir}...")
    subprocess.run(
        [
            "git", "clone",
            "--depth", "1",
            "--branch", f"v{version}",
            WLED_GIT_URL,
            str(wled_dir),
        ],
        check=True,
    )
    return wled_dir


def _find_firmware_binary(wled_dir: Path, env_name: str) -> Path | None:
    """Locate the built firmware binary for an environment.

    WLED's extra_scripts put release binaries in build_output/release/.
    Falls back to PlatformIO's default output location.
    """
    release_dir = wled_dir / "build_output" / "release"
    if release_dir.exists():
        bins = list(release_dir.glob("*.bin"))
        if bins:
            return max(bins, key=lambda p: p.stat().st_mtime)

    pio_bin = wled_dir / ".pio" / "build" / env_name / "firmware.bin"
    if pio_bin.exists():
        return pio_bin

    return None


def _run_single_build(
    wled_dir: Path,
    env_name: str,
    output_dir: Path,
    log_dir: Path,
) -> Path | None:
    """Run PlatformIO build for one environment, streaming output to both
    the terminal and a log file for the audit trail."""
    print(f"\n{'=' * 60}")
    print(f"Building: {env_name}")
    print(f"{'=' * 60}")

    log_path = log_dir / f"build_{env_name}.log"

    with open(log_path, "w") as log_file:
        process = subprocess.Popen(
            ["pio", "run", "-e", env_name],
            cwd=str(wled_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in process.stdout:
            sys.stdout.write(line)
            log_file.write(line)
        process.wait()

    if process.returncode != 0:
        print(f"FAILED: {env_name} (exit code {process.returncode})")
        return None

    binary = _find_firmware_binary(wled_dir, env_name)
    if binary is None:
        print(f"WARNING: No binary found for {env_name}")
        return None

    dest = output_dir / binary.name
    dest.write_bytes(binary.read_bytes())
    print(f"OK: {dest.name} ({dest.stat().st_size:,} bytes)")

    # Clean build artifacts to save disk for the next environment
    pio_build = wled_dir / ".pio" / "build" / env_name
    if pio_build.exists():
        subprocess.run(["rm", "-rf", str(pio_build)], check=False)
    release_dir = wled_dir / "build_output" / "release"
    if release_dir.exists():
        for f in release_dir.iterdir():
            f.unlink()

    return dest


def build_version(
    version: str,
    output_base: Path,
    source: str = "all",
):
    """Build WLED firmware for a version.

    Args:
        version: WLED version string (e.g. "0.15.4")
        output_base: Root directory for build output
        source: "generic", "quinled", or "all"
    """
    output_dir = output_base / version
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    audit_dir = output_dir / "audit"
    audit_dir.mkdir(exist_ok=True)

    wled_commit = get_wled_commit_sha(version)
    print(f"WLED v{version} commit: {wled_commit}")

    with tempfile.TemporaryDirectory(prefix="wled-build-") as tmp:
        tmp_path = Path(tmp)

        if source in ("generic", "all"):
            _build_generic(version, wled_commit, tmp_path, output_dir, log_dir, audit_dir)

        if source in ("quinled", "all"):
            _build_quinled(version, wled_commit, tmp_path, output_dir, log_dir, audit_dir)

    _write_checksums(output_dir)
    print(f"\nBuild complete. Output in: {output_dir}")


def _build_generic(
    version: str,
    wled_commit: str,
    tmp_path: Path,
    output_dir: Path,
    log_dir: Path,
    audit_dir: Path,
):
    """Build generic WLED targets with WireGuard patched in."""
    print("\n--- Generic WLED builds ---")
    wled_dir = clone_wled(version, tmp_path / "generic")

    ini_path = wled_dir / "platformio.ini"
    ini_content = ini_path.read_text()
    result = patch_ini(ini_content)

    # Audit: save both original and patched INI
    (audit_dir / "generic_original_platformio.ini").write_text(result.original)
    (audit_dir / "generic_patched_platformio.ini").write_text(result.patched)

    ini_path.write_text(result.patched)

    print(f"Patched envs (WG added): {', '.join(result.patched_envs)}")
    print(f"Skipped envs (ESP8266 or already has WG): {', '.join(result.skipped_envs)}")

    envs = get_default_envs(result.patched)
    records: list[BuildRecord] = []

    for env_name in envs:
        binary_path = _run_single_build(wled_dir, env_name, output_dir, log_dir)
        has_wg = env_name in result.patched_envs
        records.append(BuildRecord(
            version=version,
            env_name=env_name,
            source="generic",
            wled_commit=wled_commit,
            quinled_commit=None,
            wireguard=has_wg,
            original_ini_sha256=sha256_str(result.original),
            patched_ini_sha256=sha256_str(result.patched),
            binary_sha256=sha256_file(binary_path) if binary_path else None,
            binary_filename=binary_path.name if binary_path else None,
            binary_size=binary_path.stat().st_size if binary_path else None,
            build_success=binary_path is not None,
            build_log=f"logs/build_{env_name}.log",
            built_at=datetime.now(timezone.utc).isoformat(),
        ))

    write_build_record(audit_dir / "generic_builds.json", records)
    return records


def _build_quinled(
    version: str,
    wled_commit: str,
    tmp_path: Path,
    output_dir: Path,
    log_dir: Path,
    audit_dir: Path,
):
    """Build QuinLED targets with WireGuard patched in."""
    print("\n--- QuinLED builds ---")

    override_content = fetch_quinled_override(version)
    if override_content is None:
        print(f"No QuinLED platformio_override.ini found for v{version}, skipping.")
        return []

    quinled_commit = get_quinled_commit_sha(version)
    print(f"QuinLED v{version} commit: {quinled_commit}")

    wled_dir = clone_wled(version, tmp_path / "quinled")

    result = patch_ini(override_content)

    # Audit: save both original and patched override
    (audit_dir / "quinled_original_override.ini").write_text(result.original)
    (audit_dir / "quinled_patched_override.ini").write_text(result.patched)

    (wled_dir / "platformio_override.ini").write_text(result.patched)

    print(f"Patched envs (WG added): {', '.join(result.patched_envs)}")
    print(f"Skipped envs (ESP8266 or already has WG): {', '.join(result.skipped_envs)}")

    envs = get_default_envs(result.patched)
    records: list[BuildRecord] = []

    for env_name in envs:
        binary_path = _run_single_build(wled_dir, env_name, output_dir, log_dir)
        has_wg = env_name in result.patched_envs
        records.append(BuildRecord(
            version=version,
            env_name=env_name,
            source="quinled",
            wled_commit=wled_commit,
            quinled_commit=quinled_commit,
            wireguard=has_wg,
            original_ini_sha256=sha256_str(result.original),
            patched_ini_sha256=sha256_str(result.patched),
            binary_sha256=sha256_file(binary_path) if binary_path else None,
            binary_filename=binary_path.name if binary_path else None,
            binary_size=binary_path.stat().st_size if binary_path else None,
            build_success=binary_path is not None,
            build_log=f"logs/build_{env_name}.log",
            built_at=datetime.now(timezone.utc).isoformat(),
        ))

    write_build_record(audit_dir / "quinled_builds.json", records)
    return records


def _write_checksums(output_dir: Path):
    """Write a SHA-256 checksums file for all binaries in the output directory."""
    bins = sorted(output_dir.glob("*.bin"))
    if not bins:
        return
    checksum_path = output_dir / "checksums.sha256"
    with open(checksum_path, "w") as f:
        for bin_file in bins:
            digest = sha256_file(bin_file)
            f.write(f"{digest}  {bin_file.name}\n")
    print(f"Checksums written: {checksum_path} ({len(bins)} files)")
