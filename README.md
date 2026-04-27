# wled-builds

Pre-built [WLED](https://github.com/wled/WLED) firmware with [WireGuard VPN](https://github.com/wled/WLED/blob/main/usermods/wireguard/readme.md) baked in.

Covers both the generic WLED targets and [QuinLED](https://github.com/intermittech/QuinLED-Firmware) board configs. We don't fork either project — we clone upstream at their release tags, patch in the WireGuard usermod, and build. That's it.

WireGuard only works on ESP32. ESP8266 builds are included but ship without it (the chip can't handle the crypto).

## Why

WLED doesn't ship WireGuard in its release binaries. If you want your controllers on a VPN you have to build from source yourself. This repo automates that so you don't have to.

## What gets built

For each WLED release (>= 0.15.4):

- **Generic targets** — the same set of environments WLED builds for their own releases, plus WireGuard on all ESP32 variants
- **QuinLED targets** — if QuinLED has published a matching release, their board configs get built with WireGuard too

## Trust / verification

Every build produces:

- `checksums.sha256` — SHA-256 hashes for every binary
- `audit/` — the original upstream INI and the patched version we actually built, plus a JSON record of every environment (commit SHAs, hashes, success/failure)
- `logs/` — full PlatformIO build output per environment

The original and patched INI files are both saved so you can diff them and confirm the only change is WireGuard being added.

## Usage

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### Check what needs building

```bash
uv run wled-build check
```

### Build a specific version

```bash
uv run wled-build build 0.15.4
uv run wled-build build 0.15.4 --source quinled   # just QuinLED targets
uv run wled-build build 0.15.4 --source generic    # just generic WLED targets
```

### Build everything that's missing

```bash
uv run wled-build build-new
```

Builds need [PlatformIO](https://platformio.org/) installed (`pip install platformio`). Set `GITHUB_TOKEN` to avoid API rate limits.

## CI

The GitHub Actions workflow runs daily and on manual dispatch. It calls `build-new`, so it only builds versions that aren't already in the manifest.
