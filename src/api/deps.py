from os import environ

from fastapi import Request

AUTH_HEADER = environ.get("AUTH_HEADER", "Remote-User")
DEFAULT_USER = environ.get("DEFAULT_USER", "anonymous")
# Authelia already forwards LDAP group membership as this header for every
# service behind forward-auth (comma-separated -- see stack's
# authelia--middleware.yaml authResponseHeaders) -- no Authelia/Traefik/LLDAP
# change needed to read it, mediarvester just wasn't looking at it before.
AUTH_GROUPS_HEADER = environ.get("AUTH_GROUPS_HEADER", "Remote-Groups")
# Comma-separated -- any one of these forwarded groups grants admin. Lets the
# stack point this at mediarvester_admin/media_admin/admins (its per-tool,
# category, and global-superuser groups -- see values/default/security/
# access-groups.yaml in platypod/stack) without this app knowing those names
# are related; it just checks for overlap.
ADMIN_GROUPS = {g.strip() for g in environ.get("ADMIN_GROUPS", "admins").split(",") if g.strip()}


def get_current_user(request: Request) -> str:
    return request.headers.get(AUTH_HEADER, DEFAULT_USER)


def is_admin(request: Request) -> bool:
    """True when the requester's forwarded LDAP groups include any of ADMIN_GROUPS.
    Admins see and manage every owner's Downloads/Sources/MediaItems, not
    just their own -- see the `admin` param each router checks alongside
    `owner` before applying (or skipping) the usual `.owner == owner`
    scoping."""
    groups = request.headers.get(AUTH_GROUPS_HEADER, "")
    forwarded = {g.strip() for g in groups.split(",") if g.strip()}
    return not ADMIN_GROUPS.isdisjoint(forwarded)
