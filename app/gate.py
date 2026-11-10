"""The approval gate's token: proof that the athlete approved this proposal against this plan."""
import hashlib
import hmac
import secrets

from app import workspace


def key() -> bytes:
    """Kept outside the workspace, where no agent can read it."""
    path = workspace.state() / "secrets" / "approval_key"
    if not path.exists():
        with workspace.lock():
            if not path.exists():
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                workspace.write_file(path, secrets.token_bytes(32), mode=0o600)
    return path.read_bytes()


def token(proposal_id: str, base_commit: str) -> str:
    return hmac.new(key(), f"{proposal_id}:{base_commit}".encode(), hashlib.sha256).hexdigest()


def valid(given: str, proposal_id: str, base_commit: str) -> bool:
    return hmac.compare_digest((given or "").encode(), token(proposal_id, base_commit).encode())
