"""Create GitHub artifact attestations using sigstore.

Runs inside GitHub Actions. Uses the runner's OIDC identity to sign,
then POSTs the sigstore bundle to GitHub's attestation API.

Requires workflow permissions: id-token: write, attestations: write
"""

import json
import os
from pathlib import Path

import requests


def _get_oidc_token() -> str:
    """Get an OIDC identity token from the GitHub Actions runner."""
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")

    if not request_url or not request_token:
        raise RuntimeError(
            "OIDC token not available. "
            "Are you running inside GitHub Actions with id-token: write permission?"
        )

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {request_token}"
    resp = session.get(f"{request_url}&audience=sigstore", timeout=30)
    resp.raise_for_status()
    # Clear auth before any exception traceback could capture the session
    session.close()
    return resp.json()["value"]


def attest_file(file_path: Path) -> bool:
    """Sign a file with sigstore and register the attestation with GitHub.

    Returns True if attestation was created, False if not running in CI
    (skips gracefully for local builds).
    """
    # Skip gracefully outside of GitHub Actions
    if not os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"):
        print(f"  Attestation: skipped (not running in GitHub Actions)")
        return False

    from sigstore.models import ClientTrustConfig
    from sigstore.oidc import IdentityToken
    from sigstore.sign import SigningContext

    # Step 1: Get OIDC token from the runner
    raw_token = _get_oidc_token()
    identity_token = IdentityToken(raw_token)

    # Step 2: Sign the file with sigstore (Fulcio cert + Rekor log)
    trust_config = ClientTrustConfig.production()
    signing_ctx = SigningContext.from_trust_config(trust_config)
    with signing_ctx.signer(identity_token) as signer:
        bundle = signer.sign_artifact(file_path.read_bytes())

    # Step 3: POST the attestation bundle to GitHub
    repo = os.environ.get("GITHUB_REPOSITORY")
    gh_token = os.environ.get("GITHUB_TOKEN")

    if not repo or not gh_token:
        raise RuntimeError("GITHUB_REPOSITORY and GITHUB_TOKEN must be set")

    bundle_json = json.loads(bundle.to_json())

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {gh_token}"
    session.headers["Accept"] = "application/vnd.github+json"
    resp = session.post(
        f"https://api.github.com/repos/{repo}/attestations",
        json={"bundle": bundle_json},
        timeout=30,
    )
    session.close()
    if not resp.ok:
        print(f"  Attestation API error {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    print(f"  Attestation: created")
    return True
