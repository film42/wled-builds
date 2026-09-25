"""Patch PlatformIO INI files to add WireGuard support to ESP32 environments.

The patching is purely text-based to preserve original formatting, comments,
and PlatformIO-specific interpolation syntax (${section.key}).
"""

import re
from dataclasses import dataclass, field

WG_BUILD_FLAG = "-D USERMOD_WIREGUARD"
WG_LIB_DEP = "https://github.com/kienvu58/WireGuard-ESP32-Arduino.git"
# WLED 16+ compiles usermods only when listed in custom_usermods; the
# USERMOD_WIREGUARD flag is ignored there.
WG_USERMOD = "wireguard"

# Substrings that indicate ESP8266 hardware (not WireGuard-capable)
_ESP8266_INDICATORS = frozenset(
    [
        "esp8266",
        "esp01",
        "esp02",
        "nodemcu",
        "d1_mini",
        "d1mini",
    ]
)


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


def _make_parser():
    import configparser

    parser = configparser.RawConfigParser(
        inline_comment_prefixes=(),  # don't eat # or ; mid-value
        comment_prefixes=("#", ";"),  # only full-line comments
        strict=False,
    )
    parser.optionxform = str  # preserve case (PlatformIO is case-sensitive)
    return parser


def _strip_inline_comment(line: str) -> str:
    """Drop PlatformIO-style inline comments (whitespace then ; or #)."""
    return re.sub(r"\s+[;#].*$", "", line).strip()


def _effective_option(parser, section: str, key: str) -> str | None:
    """Resolve an option the way PlatformIO does: own value, then each
    `extends` parent in order, then the [env] base section for env:* sections."""
    seen: set[str] = set()

    def resolve(sec: str) -> str | None:
        if sec in seen or not parser.has_section(sec):
            return None
        seen.add(sec)
        if parser.has_option(sec, key):
            return parser.get(sec, key)
        if parser.has_option(sec, "extends"):
            for parent in parser.get(sec, "extends").split(","):
                value = resolve(parent.strip())
                if value is not None:
                    return value
        return None

    value = resolve(section)
    if value is None and section.startswith("env:") and parser.has_option("env", key):
        value = parser.get("env", key)
    return value


def _add_wireguard_usermod(
    section_lines: list[str], inherited: str | None
) -> list[str]:
    """Set custom_usermods in this section to its effective value plus wireguard."""
    entries = [_strip_inline_comment(l) for l in (inherited or "").split("\n")]
    entries = [e for e in entries if e]
    tokens = " ".join(entries).split()
    if WG_USERMOD in tokens or "*" in tokens:
        return list(section_lines)

    result = list(section_lines)
    end = _find_value_end(result, "custom_usermods")
    if end is not None:
        start = next(
            i
            for i in range(end, -1, -1)
            if re.match(r"^\s*custom_usermods\s*=", result[i])
        )
        del result[start : end + 1]
        insert_at = start
    else:
        insert_at = 1

    # Keep one entry per line so external URL entries stay intact
    new_lines = [f"custom_usermods = {entries[0]}" if entries else "custom_usermods ="]
    new_lines += [f"  {e}" for e in entries[1:]]
    new_lines.append(f"  {WG_USERMOD}")
    result[insert_at:insert_at] = new_lines
    return result


def patch_ini(
    content: str,
    wg_usermod: bool = False,
    base_ini: str | None = None,
) -> PatchResult:
    """Add WireGuard to all ESP32-based [env:*] sections in a PlatformIO INI.

    ESP8266 environments and environments that already have WireGuard are skipped.
    Non-env sections ([platformio], [common], etc.) are passed through unchanged.

    With wg_usermod (WLED 16+), WireGuard is added to each env's effective
    custom_usermods instead of via build flag. base_ini is the platformio.ini
    that `content` layers on top of (for platformio_override.ini), used to
    resolve inherited values.
    """
    parser = None
    if wg_usermod:
        parser = _make_parser()
        if base_ini is not None:
            parser.read_string(base_ini)
        parser.read_string(content)

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
        elif wg_usermod:
            inherited = _effective_option(parser, f"env:{env_name}", "custom_usermods")
            patched = _add_wireguard_usermod(section_lines, inherited)
            patched_envs.append(env_name)
            result_lines.extend(patched)
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
    """Extract default_envs from a PlatformIO INI string using configparser.

    Uses RawConfigParser with inline comments disabled so that PlatformIO's
    ${section.key} interpolation and # inside values are left alone.
    """
    import configparser

    parser = configparser.RawConfigParser(
        inline_comment_prefixes=(),  # don't eat # or ; mid-value
        comment_prefixes=("#", ";"),  # only full-line comments
    )
    parser.optionxform = str  # preserve case (PlatformIO is case-sensitive)

    try:
        parser.read_string(ini_content)
    except configparser.Error as e:
        raise ValueError(f"Failed to parse INI: {e}") from e

    if parser.has_option("platformio", "default_envs"):
        raw = parser.get("platformio", "default_envs")
        # May be comma-separated, newline-separated, or both
        names = raw.replace(",", "\n").split("\n")
        return [n.strip() for n in names if n.strip()]

    # Fallback: all [env:*] sections
    return [s[4:] for s in parser.sections() if s.startswith("env:")]


_USERMOD_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def fix_usermod_case(
    ini_content: str, usermod_names: list[str]
) -> tuple[str, list[str]]:
    """Rewrite custom_usermods tokens to match the real usermods/ folder casing.

    WLED's load_usermods.py looks up folders with an exact-case path check, so
    a name like "temperature" only resolves to usermods/Temperature on
    case-insensitive filesystems (Windows/macOS). QuinLED's override uses
    lowercase names, which breaks on Linux CI.

    Returns the updated content and a list of "old -> new" fixes applied.
    """
    existing = set(usermod_names)
    by_lower = {n.lower(): n for n in usermod_names}
    fixes: list[str] = []

    def resolve(tok: str) -> str:
        # Mirror find_usermod's candidate order: name, name_v2, usermod_v2_name
        candidates = [tok, f"{tok}_v2", f"usermod_v2_{tok}"]
        if any(c in existing for c in candidates):
            return tok
        for c in candidates:
            actual = by_lower.get(c.lower())
            if actual:
                fixes.append(f"{tok} -> {actual}")
                return actual
        return tok

    def fix_value(value: str) -> str:
        # Leave URLs and "name = spec" entries alone; only touch bare names
        return " ".join(
            resolve(t) if _USERMOD_TOKEN.fullmatch(t) else t for t in value.split(" ")
        )

    key_re = re.compile(r"^(\s*custom_usermods\s*=)(.*)$")
    lines = ini_content.split("\n")
    in_value = False
    for i, line in enumerate(lines):
        m = key_re.match(line)
        if m:
            lines[i] = m.group(1) + fix_value(m.group(2))
            in_value = True
        elif (
            in_value
            and line
            and line[0].isspace()
            and not line.strip().startswith(("#", ";"))
        ):
            lines[i] = fix_value(line)
        else:
            in_value = False

    return "\n".join(lines), fixes
