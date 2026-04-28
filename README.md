# wled-builds

Pre-built [WLED](https://github.com/wled/WLED) firmware with [WireGuard VPN](https://github.com/wled/WLED/blob/main/usermods/wireguard/readme.md) baked in.

Covers both the generic WLED targets and [QuinLED](https://github.com/intermittech/QuinLED-Firmware) board configs. We don't fork either project — we clone upstream at their release tags, patch in the WireGuard usermod, and build. That's it.

WireGuard only works on ESP32. ESP8266 targets are skipped entirely.

## Why

WLED doesn't ship WireGuard in its release binaries. If you want your controllers on a VPN you have to build from source yourself. This repo automates that so you don't have to.

## What gets built

For each WLED release (>= 0.15.4, including betas):

- **Generic targets** — the same ESP32 environments WLED builds for their own releases, plus WireGuard
- **QuinLED targets** — if QuinLED has published a matching release, their board configs get built with WireGuard too

## Where builds end up

- **GitHub Releases** on this repo — one release per WLED version, binaries + build logs as assets
- **R2** — same files at `v{version}/{source}/{env}.bin` for programmatic access

For each environment, the flow is: build → attest → publish. A binary is never uploaded until it has a sigstore attestation. If a build is re-run, envs that already have assets on the release are skipped entirely (no build, no wasted time).

## Trust / verification

Every binary is attested using [GitHub artifact attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) (sigstore). To verify a binary you downloaded:

```bash
gh attestation verify WLED_0.15.4_ESP32.bin --repo film42/wled-builds
```

Or programmatically, take the SHA-256 of the file and query:

```
GET https://api.github.com/repos/film42/wled-builds/attestations/sha256:{hash}
```

If GitHub returns an attestation, the binary was built by this repo's CI. If not, don't trust it.

Each build also produces a build log with a provenance header showing the exact upstream commit SHAs and links:

```
Source: wled/WLED @ v0.15.4 (9af566ff877fe2f478ec5e4ba3b0940e5a83cd77)
  https://github.com/wled/WLED/tree/9af566ff877fe2f478ec5e4ba3b0940e5a83cd77
Config: intermittech/QuinLED-Firmware @ v0.15.4 (a814028...)
  https://github.com/intermittech/QuinLED-Firmware/tree/a814028...
Environment: dig-Quad-V3
WireGuard: yes
---
<full PlatformIO build output>
```

The original and patched INI files are saved in the audit artifacts so you can diff them and confirm WireGuard is the only change.

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
uv run wled-build build 0.15.4 --source quinled
```

### Build everything that's missing

```bash
uv run wled-build build-new
```

Builds need [PlatformIO](https://platformio.org/) installed (`pip install platformio`).

Set `GITHUB_TOKEN` for release creation and to avoid API rate limits. R2 uploads need `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET`.

## CI

The GitHub Actions workflow runs daily at 6am UTC and on manual dispatch. It builds any missing versions, publishes to GitHub Releases and R2, and attests each binary with sigstore. Already-published envs are skipped automatically.
