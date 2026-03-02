OWNER = "owner"
ADMIN = "admin"
MEMBER = "member"
VIEWER = "viewer"


ROLE_PRIORITY = {
    VIEWER: 10,
    MEMBER: 20,
    ADMIN: 30,
    OWNER: 40,
}


def has_min_role(role: str, min_role: str) -> bool:
    return ROLE_PRIORITY.get(role, 0) >= ROLE_PRIORITY.get(min_role, 0)


def require_owner(role: str) -> bool:
    return role == OWNER


def require_admin(role: str) -> bool:
    return has_min_role(role, ADMIN)


def require_member(role: str) -> bool:
    return has_min_role(role, MEMBER)


def require_viewer(role: str) -> bool:
    return has_min_role(role, VIEWER)
