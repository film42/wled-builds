"""Query GitHub API for upstream WLED and QuinLED releases.

All GitHub API calls go through authenticated requests when GITHUB_TOKEN
is set, falling back to unauthenticated (rate-limited) otherwise.
"""

import os

import requests

WLED_REPO = "wled/WLED"
QUINLED_REPO = "intermittech/QuinLED-Firmware"
GITHUB_API = "https://api.github.com"


def _headers() -> dict[str, str]:
    """Build GitHub API headers, with auth if token is available."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url: str, **kwargs) -> requests.Response:
    """Make an authenticated GET request to the GitHub API."""
    resp = requests.get(url, headers=_headers(), timeout=30, **kwargs)
    resp.raise_for_status()
    return resp


def list_releases(repo: str) -> list[dict]:
    """List all non-draft releases for a GitHub repo."""
    releases = []
    page = 1
    while True:
        resp = _get_json(
            f"{GITHUB_API}/repos/{repo}/releases",
            params={"per_page": 100, "page": page},
        )
        batch = resp.json()
        if not batch:
            break
        releases.extend(batch)
        page += 1
    return [r for r in releases if not r["draft"]]


def get_latest_stable(repo: str) -> dict | None:
    """Get the release GitHub considers 'Latest' (the green badge on the releases page)."""
    try:
        resp = _get_json(f"{GITHUB_API}/repos/{repo}/releases/latest")
        r = resp.json()
        return {
            "tag": r["tag_name"],
            "version": r["tag_name"].lstrip("v"),
            "published_at": r["published_at"],
        }
    except Exception:
        return None


def get_wled_releases() -> list[dict]:
    """Get all WLED release versions."""
    return [
        {
            "tag": r["tag_name"],
            "version": r["tag_name"].lstrip("v"),
            "published_at": r["published_at"],
            "prerelease": r["prerelease"],
        }
        for r in list_releases(WLED_REPO)
    ]


def get_quinled_releases() -> list[dict]:
    """Get all QuinLED release versions."""
    return [
        {
            "tag": r["tag_name"],
            "version": r["tag_name"].lstrip("v"),
            "published_at": r["published_at"],
            "prerelease": r["prerelease"],
        }
        for r in list_releases(QUINLED_REPO)
    ]


def fetch_file_at_tag(repo: str, tag: str, path: str) -> str | None:
    """Fetch a raw file from a repo at a specific git tag. Returns None on 404."""
    url = f"https://raw.githubusercontent.com/{repo}/{tag}/{path}"
    resp = requests.get(url, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


def fetch_wled_platformio_ini(version: str) -> str | None:
    """Fetch WLED's platformio.ini at a specific version tag."""
    return fetch_file_at_tag(WLED_REPO, f"v{version}", "platformio.ini")


def fetch_quinled_override(version: str) -> str | None:
    """Fetch QuinLED's platformio_override.ini at a specific version tag."""
    return fetch_file_at_tag(QUINLED_REPO, f"v{version}", "platformio_override.ini")


def _resolve_tag_sha(repo: str, tag: str) -> str | None:
    """Resolve a git tag to its commit SHA, handling both lightweight and annotated tags."""
    try:
        resp = _get_json(f"{GITHUB_API}/repos/{repo}/git/ref/tags/{tag}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise
    data = resp.json()
    # Annotated tags have an extra indirection
    if data["object"]["type"] == "tag":
        resp = _get_json(data["object"]["url"])
        return resp.json()["object"]["sha"]
    return data["object"]["sha"]


def get_wled_commit_sha(version: str) -> str:
    """Get the exact commit SHA for a WLED version tag."""
    sha = _resolve_tag_sha(WLED_REPO, f"v{version}")
    if sha is None:
        raise ValueError(f"WLED tag v{version} not found")
    return sha


def get_quinled_commit_sha(version: str) -> str | None:
    """Get the exact commit SHA for a QuinLED version tag, or None if missing."""
    return _resolve_tag_sha(QUINLED_REPO, f"v{version}")
