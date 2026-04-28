"""Clone WLED, apply WireGuard patches, and build firmware.

Flow:
1. Check if asset already exists on the GitHub Release — skip build if so
2. Build with PlatformIO, save binary + build log to output dir
3. (Workflow attests binaries via actions/attest-build-provenance)
4. Publish step uploads attested binaries to GitHub Release + R2
"""

import hashlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .patcher import get_default_envs, patch_ini
from .attest import attest_file
from .publish import (
    asset_filename,
    get_existing_assets,
    get_or_create_release,
    upload_release_asset,
    upload_to_r2,
)
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
    """Locate the built firmware binary for an environment."""
    release_dir = wled_dir / "build_output" / "release"
    if release_dir.exists():
        bins = list(release_dir.glob("*.bin"))
        if bins:
            return max(bins, key=lambda p: p.stat().st_mtime)

    pio_bin = wled_dir / ".pio" / "build" / env_name / "firmware.bin"
    if pio_bin.exists():
        return pio_bin

    return None


def _write_build_log_header(
    log_file,
    version: str,
    source: str,
    env_name: str,
    wled_commit: str,
    quinled_commit: str | None,
):
    """Write provenance header to the build log before PlatformIO output."""
    log_file.write(f"Source: wled/WLED @ v{version} ({wled_commit})\n")
    log_file.write(f"  https://github.com/wled/WLED/tree/{wled_commit}\n")
    if quinled_commit:
        log_file.write(f"Config: intermittech/QuinLED-Firmware @ v{version} ({quinled_commit})\n")
        log_file.write(f"  https://github.com/intermittech/QuinLED-Firmware/tree/{quinled_commit}\n")
    log_file.write(f"Environment: {env_name}\n")
    log_file.write(f"Vendor: {source}\n")
    log_file.write(f"WireGuard: yes\n")
    log_file.write(f"Built: {datetime.now(timezone.utc).isoformat()}\n")
    log_file.write(f"---\n\n")


def _run_single_build(
    wled_dir: Path,
    env_name: str,
    output_path: Path,
    log_path: Path,
    version: str,
    source: str,
    wled_commit: str,
    quinled_commit: str | None,
) -> bool:
    """Build one environment, copy binary to output_path, stream to log file.

    Returns True on success, False on failure.
    """
    print(f"\n{'=' * 60}")
    print(f"Building: {env_name}")
    print(f"{'=' * 60}")

    with open(log_path, "w") as log_file:
        _write_build_log_header(log_file, version, source, env_name, wled_commit, quinled_commit)

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
        raise RuntimeError(f"pio run failed for {env_name} (exit code {process.returncode})")

    binary = _find_firmware_binary(wled_dir, env_name)
    if binary is None:
        raise RuntimeError(f"No binary found for {env_name} after successful build")

    # Copy binary out BEFORE cleaning build artifacts
    output_path.write_bytes(binary.read_bytes())
    print(f"OK: {output_path.name} ({output_path.stat().st_size:,} bytes)")

    # Clean build artifacts to save disk for next env
    pio_build = wled_dir / ".pio" / "build" / env_name
    if pio_build.exists():
        subprocess.run(["rm", "-rf", str(pio_build)], check=False)
    release_dir = wled_dir / "build_output" / "release"
    if release_dir.exists():
        for f in release_dir.iterdir():
            f.unlink()


def build_version(
    version: str,
    output_base: Path,
    source: str = "all",
):
    """Build and publish WLED firmware for a version.

    For each env: check if already published, build if not, upload everywhere.
    """
    output_dir = output_base / version
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    audit_dir = output_dir / "audit"
    audit_dir.mkdir(exist_ok=True)

    wled_commit = get_wled_commit_sha(version)
    print(f"WLED v{version} commit: {wled_commit}")

    # Get or create the GitHub Release for this version
    release = get_or_create_release(version)
    existing_assets = get_existing_assets(release)
    print(f"Existing release assets: {len(existing_assets)}")

    with tempfile.TemporaryDirectory(prefix="wled-build-") as tmp:
        tmp_path = Path(tmp)

        if source in ("generic", "all"):
            _build_source(
                version=version,
                source_name="generic",
                wled_commit=wled_commit,
                quinled_commit=None,
                tmp_path=tmp_path,
                output_dir=output_dir,
                log_dir=log_dir,
                audit_dir=audit_dir,
                release=release,
                existing_assets=existing_assets,
            )

        if source in ("quinled", "all"):
            quinled_commit = get_quinled_commit_sha(version)
            if quinled_commit is None:
                print(f"\nNo QuinLED release for v{version}, skipping.")
            else:
                _build_source(
                    version=version,
                    source_name="quinled",
                    wled_commit=wled_commit,
                    quinled_commit=quinled_commit,
                    tmp_path=tmp_path,
                    output_dir=output_dir,
                    log_dir=log_dir,
                    audit_dir=audit_dir,
                    release=release,
                    existing_assets=existing_assets,
                )

    print(f"\nBuild complete. Output in: {output_dir}")


def _build_source(
    version: str,
    source_name: str,
    wled_commit: str,
    quinled_commit: str | None,
    tmp_path: Path,
    output_dir: Path,
    log_dir: Path,
    audit_dir: Path,
    release: dict,
    existing_assets: set[str],
):
    """Build all envs for a source (generic or quinled)."""
    print(f"\n--- {source_name} builds ---")

    wled_dir = clone_wled(version, tmp_path / source_name)

    # Get and patch the INI
    if source_name == "quinled":
        override_content = fetch_quinled_override(version)
        if override_content is None:
            print(f"No QuinLED platformio_override.ini found for v{version}.")
            return
        result = patch_ini(override_content)
        (audit_dir / "quinled_original_override.ini").write_text(result.original)
        (audit_dir / "quinled_patched_override.ini").write_text(result.patched)
        (wled_dir / "platformio_override.ini").write_text(result.patched)
    else:
        ini_path = wled_dir / "platformio.ini"
        ini_content = ini_path.read_text()
        result = patch_ini(ini_content)
        (audit_dir / "generic_original_platformio.ini").write_text(result.original)
        (audit_dir / "generic_patched_platformio.ini").write_text(result.patched)
        ini_path.write_text(result.patched)

    print(f"Patched envs (WG added): {', '.join(result.patched_envs)}")
    print(f"Skipped envs: {', '.join(result.skipped_envs)}")

    # Only build ESP32 envs (the ones that got WG patched in)
    all_envs = get_default_envs(result.patched)
    envs = [e for e in all_envs if e in result.patched_envs]
    skipped_8266 = [e for e in all_envs if e not in result.patched_envs]
    if skipped_8266:
        print(f"Skipping non-ESP32 envs: {', '.join(skipped_8266)}")

    for env_name in envs:
        bin_asset = asset_filename(source_name, version, env_name, ".bin")
        if bin_asset in existing_assets:
            print(f"\nSkipping {env_name} — {bin_asset} already in release")
            continue

        log_path = log_dir / f"{source_name}_{env_name}.build_log.txt"
        dest = output_dir / asset_filename(source_name, version, env_name, ".bin")

        _run_single_build(
            wled_dir, env_name, dest, log_path,
            version, source_name, wled_commit, quinled_commit,
        )

        digest = sha256_file(dest)
        print(f"  SHA256: {digest}")

        # Attest BEFORE publishing — never serve an unattested binary
        attest_file(dest)

        # Publish to GitHub Release + R2
        bin_name = asset_filename(source_name, version, env_name, ".bin")
        log_name = asset_filename(source_name, version, env_name, ".build_log.txt")

        if upload_release_asset(release, str(dest), bin_name):
            print(f"  Release: uploaded {bin_name}")
        upload_release_asset(release, str(log_path), log_name)

        r2_prefix = f"v{version}/{source_name}"
        upload_to_r2(str(dest), f"{r2_prefix}/{env_name}.bin")
        upload_to_r2(str(log_path), f"{r2_prefix}/{env_name}.build_log.txt")
