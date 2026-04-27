"""Patch PlatformIO INI files to add WireGuard support to ESP32 environments.

The patching is purely text-based to preserve original formatting, comments,
and PlatformIO-specific interpolation syntax (${section.key}).
"""

import re
from dataclasses import dataclass, field

WG_BUILD_FLAG = "-D USERMOD_WIREGUARD"
WG_LIB_DEP = "https://github.com/kienvu58/WireGuard-ESP32-Arduino.git"

# Substrings that indicate ESP8266 hardware (not WireGuard-capable)
_ESP8266_INDICATORS = frozenset([
    "esp8266", "esp01", "esp02", "nodemcu", "d1_mini", "d1mini",
])


@dataclass
class PatchResult:
    """Result of patching an INI file."""
    original: str
    patched: str
    patched_envs: list[str] = field(default_factory=list)
    skipped_envs: list[str] = field(default_factory=list)


def _is_esp8266(section_text: str, env_name: str) -> bool:
    """Check if an environment targets ESP8266 (not WireGuard-capable)."""
    haystack = (env_name + "\n" + section_text).lower()
    return any(indicator in haystack for indicator in _ESP8266_INDICATORS)


def _has_wireguard(section_text: str) -> bool:
    """Check if WireGuard is already enabled."""
    return "USERMOD_WIREGUARD" in section_text


def _find_value_end(lines: list[str], key: str) -> int | None:
    """Find the last line index of a multi-line INI value for the given key.

    Returns the index of the last continuation line, or the key line itself
    if there are no continuations. Returns None if the key isn't found.
    """
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            end = i
            j = i + 1
            while j < len(lines):
                # Continuation lines start with whitespace
                if not lines[j] or not lines[j][0].isspace():
                    break
                # Safety: don't cross into a new section
                if lines[j].strip().startswith("["):
                    break
                end = j
                j += 1
            return end
    return None


def _get_continuation_indent(lines: list[str], value_end: int) -> str:
    """Determine the indentation used for continuation lines."""
    if value_end > 0 and lines[value_end][0].isspace():
        match = re.match(r"(\s+)", lines[value_end])
        if match:
            return match.group(1)
    return "  "


def _patch_section_lines(lines: list[str]) -> list[str]:
    """Inject WireGuard build flag and lib dependency into a single [env:] section."""
    result = list(lines)

    # Patch build_flags
    bf_end = _find_value_end(result, "build_flags")
    if bf_end is not None:
        indent = _get_continuation_indent(result, bf_end)
        result.insert(bf_end + 1, f"{indent}{WG_BUILD_FLAG}")
    else:
        # No build_flags key; insert one after the section header
        result.insert(1, f"build_flags = {WG_BUILD_FLAG}")

    # Patch lib_deps (search again since indices shifted by 1)
    ld_end = _find_value_end(result, "lib_deps")
    if ld_end is not None:
        indent = _get_continuation_indent(result, ld_end)
        result.insert(ld_end + 1, f"{indent}{WG_LIB_DEP}")
    else:
        # No lib_deps key; add one at the end of the section
        result.append(f"lib_deps =")
        result.append(f"  {WG_LIB_DEP}")

    return result


def patch_ini(content: str) -> PatchResult:
    """Add WireGuard to all ESP32-based [env:*] sections in a PlatformIO INI.

    ESP8266 environments and environments that already have WireGuard are skipped.
    Non-env sections ([platformio], [common], etc.) are passed through unchanged.
    """
    lines = content.split("\n")
    result_lines: list[str] = []
    patched_envs: list[str] = []
    skipped_envs: list[str] = []

    i = 0
    while i < len(lines):
        env_match = re.match(r"^\[env:(.+)\]", lines[i])
        if not env_match:
            result_lines.append(lines[i])
            i += 1
            continue

        env_name = env_match.group(1)

        # Collect all lines belonging to this section
        section_start = i
        i += 1
        while i < len(lines):
            stripped = lines[i].strip()
            # A new section header at column 0 ends this section
            if stripped.startswith("[") and not lines[i][0].isspace():
                break
            i += 1

        section_lines = lines[section_start:i]
        section_text = "\n".join(section_lines)

        if _is_esp8266(section_text, env_name) or _has_wireguard(section_text):
            skipped_envs.append(env_name)
            result_lines.extend(section_lines)
        else:
            patched = _patch_section_lines(section_lines)
            patched_envs.append(env_name)
            result_lines.extend(patched)

    return PatchResult(
        original=content,
        patched="\n".join(result_lines),
        patched_envs=patched_envs,
        skipped_envs=skipped_envs,
    )


def get_default_envs(ini_content: str) -> list[str]:
    """Extract the default_envs list from a PlatformIO INI.

    Handles QuinLED-style comments interleaved in the value block.
    Falls back to listing all [env:*] sections if no default_envs is found.
    """
    in_platformio = False
    in_default_envs = False
    envs: list[str] = []

    for line in ini_content.split("\n"):
        stripped = line.strip()

        if stripped.startswith("["):
            if stripped.lower() == "[platformio]":
                in_platformio = True
                in_default_envs = False
            else:
                if in_platformio:
                    break
                in_platformio = False
            continue

        if not in_platformio:
            continue

        if re.match(r"default_envs\s*=", stripped):
            in_default_envs = True
            _, _, value = stripped.partition("=")
            value = value.strip()
            if value and not value.startswith("#"):
                envs.append(value)
            continue

        if in_default_envs:
            if stripped.startswith("#") or stripped.startswith(";") or not stripped:
                continue  # skip comments and blank lines in the value block
            if line[0].isspace():
                # Continuation line — strip inline comments
                value = re.split(r"\s+#", stripped)[0].strip()
                if value:
                    envs.append(value)
            else:
                # Non-indented non-comment line = new key, value block is over
                in_default_envs = False

    if not envs:
        envs = re.findall(r"^\[env:(.+?)\]", ini_content, re.MULTILINE)

    return envs
