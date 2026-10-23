"""Map Google Drive sharing permissions onto the app's ACL principals."""


def permissions_to_acl(permissions: list[dict]) -> list[str]:
    """Every Drive role (reader, commenter, writer, owner, organizer) can read, so every grant becomes a principal.

    - user   -> user:<email>      matches SSO users, whose user_id is their email
    - group  -> group:<email>     matches when the IdP puts group emails in the token's groups claim
    - domain -> domain:<domain>   matches every user whose email is in that domain
    - anyone -> group:everyone    public or "anyone with the link"; still limited to the connector's tenant

    Anything unrecognised grants nothing, so an unexpected permission type fails closed.
    """
    acl: set[str] = set()
    for p in permissions:
        if p.get("deleted"):
            continue
        kind = p.get("type")
        if kind == "user" and p.get("emailAddress"):
            acl.add(f"user:{p['emailAddress'].lower()}")
        elif kind == "group" and p.get("emailAddress"):
            acl.add(f"group:{p['emailAddress'].lower()}")
        elif kind == "domain" and p.get("domain"):
            acl.add(f"domain:{p['domain'].lower()}")
        elif kind == "anyone":
            acl.add("group:everyone")
    return sorted(acl)
