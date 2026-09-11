"""What keeps a loopback dashboard safe from the browser it is served to.

The server has no authentication, so every defense here is about a page on
another origin reaching it through the user's browser.
"""

from http import HTTPStatus
from urllib.parse import urlparse

from fastapi import HTTPException, Request

# The only two names a page from this server can carry. Anything else is
# refused, which stops a site that points its own domain at this machine.
LOOPBACK_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Framing is denied because a click inside an attacker's iframe carries the
# dashboard's own origin, which the Origin check accepts.
SECURITY_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}


def reject_cross_origin_write(request: Request) -> None:
    """Refuse a cross-site write with 403. No `Origin` header is a non-browser
    client and passes.

    The server binds `127.0.0.1` and the Host allowlist admits two names, so a
    same-host page can only ever carry one of those two as its origin. Any port
    passes, for a dev server that proxies the API.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    try:
        host = urlparse(origin).hostname
    except ValueError:
        # A malformed Origin is still a cross-site request, not a server fault.
        host = None
    if host not in LOOPBACK_ALLOWED_HOSTS:
        raise HTTPException(HTTPStatus.FORBIDDEN, "cross-origin write rejected")
