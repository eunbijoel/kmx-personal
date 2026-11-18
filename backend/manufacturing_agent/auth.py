from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DID, VerifiableCredential

# Workbench RBAC: roles grant actions. Not a full IdP — uses existing KMX DID/VC store.
ROLE_ACTIONS: dict[str, set[str]] = {
    "approver": {"approve", "modify", "reject"},
    "reviewer": {"modify", "reject"},
    "admin": {"approve", "modify", "reject"},
}


@dataclass
class ReviewAuth:
    actor_did: str
    roles: list[str] = field(default_factory=list)
    vc_id: str | None = None
    auth_mode: str = "vc_role"
    claims: dict[str, Any] = field(default_factory=dict)

    def allows(self, action: str) -> bool:
        allowed: set[str] = set()
        for role in self.roles:
            allowed |= ROLE_ACTIONS.get(str(role).lower(), set())
        return action in allowed

    def to_history(self) -> dict[str, Any]:
        return {
            "actor_did": self.actor_did,
            "roles": list(self.roles),
            "vc_id": self.vc_id,
            "auth_mode": self.auth_mode,
        }


def extract_roles_from_claims(claims: dict[str, Any] | None) -> list[str]:
    if not claims:
        return []
    roles: list[str] = []
    for key in ("workbench_roles", "roles", "role"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            roles.append(value.strip().lower())
        elif isinstance(value, list):
            roles.extend(str(v).strip().lower() for v in value if str(v).strip())
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for role in roles:
        if role not in seen:
            seen.add(role)
            out.append(role)
    return out


def require_action(auth: ReviewAuth, action: str) -> None:
    if not auth.allows(action):
        raise PermissionError(
            f"Actor {auth.actor_did} with roles {auth.roles} cannot perform '{action}'"
        )


async def resolve_review_auth(
    db: AsyncSession,
    *,
    actor: str,
    action: str,
    vc_id: str | None = None,
) -> ReviewAuth:
    actor_did = (actor or "").strip()
    if not actor_did.startswith("did:kmx:"):
        raise PermissionError("actor must be a registered did:kmx:... identity")

    did_row = await db.scalar(select(DID).where(DID.did == actor_did, DID.active.is_(True)))
    if did_row is None:
        raise PermissionError(f"Unknown or inactive DID: {actor_did}")

    query = select(VerifiableCredential).where(
        VerifiableCredential.subject_did == actor_did,
        VerifiableCredential.revoked.is_(False),
    )
    if vc_id:
        query = query.where(VerifiableCredential.vc_id == vc_id)
    rows = (await db.scalars(query)).all()
    if not rows:
        raise PermissionError(
            f"No active workbench credential for {actor_did}"
            + (f" (vc_id={vc_id})" if vc_id else "")
        )

    # Prefer VC that grants the requested action; else first with any workbench role.
    chosen: VerifiableCredential | None = None
    chosen_roles: list[str] = []
    for row in rows:
        claims = row.claims if isinstance(row.claims, dict) else {}
        roles = extract_roles_from_claims(claims)
        if not roles:
            continue
        auth_probe = ReviewAuth(actor_did=actor_did, roles=roles, vc_id=row.vc_id)
        if auth_probe.allows(action):
            chosen = row
            chosen_roles = roles
            break
        if chosen is None:
            chosen = row
            chosen_roles = roles

    if chosen is None or not chosen_roles:
        raise PermissionError(
            f"No workbench role claims on credentials for {actor_did}. "
            "Issue a VC with claims.workbench_roles including approver|reviewer|admin."
        )

    auth = ReviewAuth(
        actor_did=actor_did,
        roles=chosen_roles,
        vc_id=chosen.vc_id,
        auth_mode="vc_role",
        claims=chosen.claims if isinstance(chosen.claims, dict) else {},
    )
    require_action(auth, action)
    return auth


def review_auth_for_tests(
    *,
    actor_did: str = "did:kmx:test:approver",
    roles: Iterable[str] = ("approver",),
    vc_id: str = "vc:kmx:test",
) -> ReviewAuth:
    """In-memory auth for unit tests (bypasses DB)."""
    return ReviewAuth(
        actor_did=actor_did,
        roles=[str(r).lower() for r in roles],
        vc_id=vc_id,
        auth_mode="test_bypass",
    )
