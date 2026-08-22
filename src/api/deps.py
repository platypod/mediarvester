from os import environ

from fastapi import Request

AUTH_HEADER = environ.get("AUTH_HEADER", "Remote-User")
DEFAULT_USER = environ.get("DEFAULT_USER", "anonymous")
# Authelia already forwards LDAP group membership as this header for every
# service behind forward-auth (comma-separated -- see stack's
# authelia--middleware.yaml authResponseHeaders) -- no Authelia/Traefik/LLDAP
# change needed to read it, mediarvester just wasn't looking at it before.
AUTH_GROUPS_HEADER = environ.get("AUTH_GROUPS_HEADER", "Remote-Groups")
ADMIN_GROUP = environ.get("ADMIN_GROUP", "admins")


def get_current_user(request: Request) -> str:
    return request.headers.get(AUTH_HEADER, DEFAULT_USER)


def is_admin(request: Request) -> bool:
    """True when the requester's forwarded LDAP groups include ADMIN_GROUP.
    Admins see and manage every owner's Downloads/Sources/MediaItems, not
    just their own -- see the `admin` param each router checks alongside
    `owner` before applying (or skipping) the usual `.owner == owner`
    scoping."""
    groups = request.headers.get(AUTH_GROUPS_HEADER, "")
    return ADMIN_GROUP in {g.strip() for g in groups.split(",") if g.strip()}
