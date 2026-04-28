"""Publish build artifacts to GitHub Releases and R2.

GitHub Releases: each WLED version gets one release. Assets are uploaded
per-env as they complete. If an asset already exists, the env is skipped
entirely (no build, no upload).

R2: files are stored at {version}/{source}/{env}.{bin,build_log.txt}.
Overwrites are fine since attestation is hash-based.
"""

import os

import requests

REPO = "film42/wled-builds"
GITHUB_API = "https://api.github.com"


class _RedactedAuth(requests.auth.AuthBase):
    """Auth handler that keeps the token out of exception tracebacks."""

    def __init__(self, token: str):
        self._token = token

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self._token}"
        return r

    def __repr__(self):
        return "RedactedAuth(***)"


def _session() -> requests.Session:
    """Create a session with auth that won't leak tokens in tracebacks."""
    s = requests.Session()
    s.headers["Accept"] = "application/vnd.github+json"
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        s.auth = _RedactedAuth(token)
    return s


def get_or_create_release(version: str) -> dict:
    """Get existing release for this version, or create one."""
    tag = f"v{version}"
    session = _session()

    resp = session.get(
        f"{GITHUB_API}/repos/{REPO}/releases/tags/{tag}",
        timeout=30,
    )
    if resp.status_code == 200:
        release = resp.json()
        print(f"Found existing release: {tag} (id={release['id']})")
        return release

    resp = session.post(
        f"{GITHUB_API}/repos/{REPO}/releases",
        json={
            "tag_name": tag,
            "name": f"WLED {version} + WireGuard",
            "body": f"WLED {version} firmware with WireGuard VPN support.\n\nBuilt automatically from upstream sources. See build logs for provenance details.",
            "draft": False,
            "prerelease": "beta" in version or "-b" in version or "-rc" in version,
        },
        timeout=30,
    )
    resp.raise_for_status()
    release = resp.json()
    print(f"Created release: {tag} (id={release['id']})")
    return release


def get_existing_assets(release: dict) -> set[str]:
    """Get the set of asset filenames already on a release."""
    session = _session()
    assets: set[str] = set()
    page = 1
    while True:
        resp = session.get(
            f"{GITHUB_API}/repos/{REPO}/releases/{release['id']}/assets",
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for asset in batch:
            assets.add(asset["name"])
        page += 1
    return assets


def asset_filename(source: str, version: str, env_name: str, ext: str) -> str:
    """Generate a consistent asset filename."""
    return f"{source}_{version}_{env_name}{ext}"


def upload_release_asset(release: dict, filepath: str, filename: str) -> bool:
    """Upload a file as a release asset. Returns False if it already exists."""
    session = _session()
    upload_url = release["upload_url"].replace("{?name,label}", "")

    with open(filepath, "rb") as f:
        data = f.read()

    content_type = "text/plain" if filename.endswith(".txt") else "application/octet-stream"
    resp = session.post(
        upload_url,
        headers={"Content-Type": content_type},
        params={"name": filename},
        data=data,
        timeout=120,
    )
    if resp.status_code == 422:
        return False
    resp.raise_for_status()
    return True


def upload_to_r2(local_path: str, r2_key: str):
    """Upload a file to R2 using S3-compatible API.

    Requires R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY env vars.
    Skips gracefully if not configured.
    """
    endpoint = os.environ.get("R2_ENDPOINT_URL")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    bucket = os.environ.get("R2_BUCKET", "wled-builds")

    if not all([endpoint, access_key, secret_key]):
        print(f"  R2 not configured, skipping upload of {r2_key}")
        return

    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    s3.upload_file(local_path, bucket, r2_key)
    print(f"  R2: uploaded {r2_key}")
