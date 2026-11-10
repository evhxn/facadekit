from __future__ import annotations

import re
import secrets

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def slugify(name: str) -> str:
    slug = _NON_SLUG.sub("-", name.lower()).strip("-")[:36]
    return slug or "project"


def unique_slug(name: str, taken: set[str]) -> str:
    base = slugify(name)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"
