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

    resp = requests.get(
        f"{request_url}&audience=sigstore",
        headers={"Authorization": f"Bearer {request_token}"},
        timeout=30,
    )
    resp.raise_for_status()
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

    from sigstore.oidc import IdentityToken
    from sigstore.sign import SigningContext

    # Step 1: Get OIDC token from the runner
    raw_token = _get_oidc_token()
    identity_token = IdentityToken(raw_token)

    # Step 2: Sign the file with sigstore (Fulcio cert + Rekor log)
    signing_ctx = SigningContext.production()
    with signing_ctx.signer(identity_token) as signer:
        bundle = signer.sign_artifact(file_path)

    # Step 3: POST the attestation bundle to GitHub
    repo = os.environ.get("GITHUB_REPOSITORY")
    gh_token = os.environ.get("GITHUB_TOKEN")

    if not repo or not gh_token:
        raise RuntimeError("GITHUB_REPOSITORY and GITHUB_TOKEN must be set")

    bundle_json = json.loads(bundle.to_json())

    resp = requests.post(
        f"https://api.github.com/repos/{repo}/attestations",
        headers={
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
        },
        json={"bundle": bundle_json},
        timeout=30,
    )
    resp.raise_for_status()

    print(f"  Attestation: created")
    return True
