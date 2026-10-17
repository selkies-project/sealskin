"""The sign-in a reverse proxy vouches for, and the check that the proxy can be believed.

A proxy that authenticates users in front of this node (Authelia, Authentik,
oauth2-proxy, or another in forward-auth mode) names the user in a request
header, `proxy_auth_user_header`, and may list the user's groups in
`proxy_auth_groups_header`. `believed` says whether the headers of a request
count:

* The connection comes from an address in `trusted_proxies`.
* The address is not a session's. Sessions sit on the node's own network, so
  a network listed whole in `trusted_proxies` would otherwise let a session's
  desktop name any user.
* The proxy replaces what a visitor sends under those names. One that passes
  a header on lets anyone write it: with the user header that is a proxy
  whose authentication was left out of this site, and with the groups header
  one that sets the user and leaves the groups to the visitor.

The last is found out two ways, both by a request for `CHECK_PATH` whose
sign-in headers hold `FORGED`, which `answer` serves and reports on:

* `check` sends one with no credentials to the node's public entrance. A
  proxy that authenticates answers for itself, or passes the request on
  without the headers.
* `check` sends another with the cookies of the request whose headers are
  in question, which are the proxy's own sign-in. The proxy passes that one
  on, with the headers it manages replaced, so a header it leaves to the
  visitor shows before the request that brought the cookies is believed.
* The web app sends one from the browser of a user the proxy signed in, for
  a proxy whose sign-in the node cannot repeat. `answer` notes a header that
  arrives as the page wrote it.

`check` ends in one of three states. `guarded`: nothing forged arrived.
`open`: a forged header arrived, to the check or from a browser within
`FORGED_SECONDS`, and header sign-ins are refused. `unreachable`: the node
could not reach its own entrance, by `public_url` or at a trusted proxy's
address, so nothing is known; header sign-ins are refused then too unless
`proxy_auth_unchecked` says to believe the proxy regardless.

A `guarded` result is kept for `GUARDED_SECONDS` and then renewed in the
background by the next request; any other is asked again after
`RETRY_SECONDS`, so a proxy that was put right is believed at once.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request

from . import cluster
from .settings import settings

logger = logging.getLogger(__name__)

#: Path the check asks for; it answers anyone and signs nobody in.
CHECK_PATH = "/api/auth/proxy"
MARK = "proxy-check"
#: What a check request holds in the sign-in headers; no user or group has this name.
FORGED = "sealskin forged"
GUARDED_SECONDS = 60
RETRY_SECONDS = 15
TIMEOUT_SECONDS = 3
#: How long a forged header that arrived from a browser keeps header sign-ins refused.
FORGED_SECONDS = 300
#: A test an administrator starts counts the forged headers that arrived this recently.
FRESH_SECONDS = 10

_result: dict[str, Any] = {}
_forged: dict[str, float] = {}
#: The cookies of the last request a proxy signed in, for renewing the check with.
_cookies = ""
_lock = asyncio.Lock()
_renewal: asyncio.Task[None] | None = None
_transport: httpx.AsyncBaseTransport | None = None


def enabled() -> bool:
    """Whether a proxy's header signs users in at all."""
    return bool(settings.proxy_auth_user_header.strip())


def headers() -> list[str]:
    """Return the names of the headers a proxy signs users in with."""
    return [name for name in (settings.proxy_auth_user_header.strip(), settings.proxy_auth_groups_header.strip()) if name]


def answer(request: Request) -> dict[str, Any]:
    """Return what a request for `CHECK_PATH` carried when it arrived here, noting forged headers.

    Returns:
        `headers` names the sign-in headers, `user` and `groups` are their
        values as they arrived, `forged` names those that arrived holding
        `FORGED`, and `trusted` says whether a trusted proxy passed the
        request on.
    """
    names = headers()
    trusted = cluster.trusted_proxy(request.headers.get("x-sealskin-remote", ""))
    forged = [name for name in names if FORGED in request.headers.get(name, "")]
    if trusted and forged:
        if not _recent():
            logger.error("The reverse proxy passed on a forged %s header: header sign-ins are refused.", ", ".join(forged))
        _forged.update(dict.fromkeys(forged, time.time()))
    user, groups = settings.proxy_auth_user_header.strip(), settings.proxy_auth_groups_header.strip()
    return {
        "sealskin": MARK,
        "headers": names,
        "user": request.headers.get(user, "") if user else "",
        "groups": request.headers.get(groups, "") if groups else "",
        "forged": forged,
        "trusted": trusted,
    }


def _recent(within: float = FORGED_SECONDS) -> list[str]:
    """Return the headers that arrived forged from a trusted proxy in the last `within` seconds."""
    now = time.time()
    return sorted(name for name, seen in _forged.items() if now - seen < within and name in headers())


def _settings_key() -> tuple[str, ...]:
    """Return the settings a result holds for."""
    return (*headers(), settings.trusted_proxies, cluster.public_url())


def _targets() -> list[tuple[str, dict[str, str], dict[str, Any], bool]]:
    """Return the ways to reach this node's entrance, as `(url, headers, extensions, verify)`.

    First the public URL as a browser resolves it, believed only under a
    certificate for that name: whatever else answers at the address, as a
    router's own page does from inside its network, is not the proxy. Where
    the node cannot reach the public URL, each single address in
    `trusted_proxies` is asked under the public name, on the public port and
    on 443; the administrator named that address, so its certificate is not
    checked.
    """
    public = urlsplit(cluster.public_url())
    host = public.hostname or ""
    port = public.port or 443
    targets: list[tuple[str, dict[str, str], dict[str, Any], bool]] = [(f"{cluster.public_url()}{CHECK_PATH}", {}, {}, True)]
    for entry in settings.trusted_proxies.split(","):
        try:
            address = ipaddress.ip_address(entry.strip())
        except ValueError:
            continue
        literal = f"[{address}]" if address.version == 6 else str(address)
        for candidate in dict.fromkeys((port, 443)):
            targets.append((f"https://{literal}:{candidate}{CHECK_PATH}", {"Host": public.netloc}, {"sni_hostname": host}, False))
    return targets


def _read(response: httpx.Response) -> tuple[str, str]:
    """Turn the entrance's answer into a state and the sentence that explains it."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("sealskin") != MARK:
        return "guarded", f"The proxy answered a request with no sign-in itself ({response.status_code})."
    forged = body.get("forged") or []
    if not forged:
        return "guarded", "The proxy passes a request with no sign-in on, without the sign-in headers it carried."
    if not body.get("trusted"):
        return "unreachable", "The public URL does not lead through a proxy in the trusted proxies."
    return "open", _passes_on(forged)


def _passes_on(forged: list[str]) -> str:
    """Say which headers the proxy passes on as a visitor wrote them."""
    return f"The proxy passes on a {', '.join(forged)} header that a visitor can write: it does not set it for this site."


async def _probe(cookies: str = "") -> tuple[str, str]:
    """Ask the entrance and return the state and its explanation.

    Args:
        cookies: The `Cookie` header of a request the proxy signed in. Where
            the request with no credentials finds the proxy guarded, one with
            these is sent too.
    """
    sent = dict.fromkeys(headers(), FORGED) | {"Accept": "application/json"}
    failure = "no public URL"
    for url, extra, extensions, verify in _targets():
        try:
            async with httpx.AsyncClient(verify=verify, timeout=TIMEOUT_SECONDS, transport=_transport) as client:
                response = await client.get(url, headers=sent | extra, extensions=extensions)
                found = _read(response)
                if found[0] == "guarded" and cookies:
                    signed_in = await client.get(url, headers=sent | extra | {"Cookie": cookies}, extensions=extensions)
                    if _read(signed_in)[0] == "open":
                        return _read(signed_in)
        except httpx.HTTPError as exc:
            failure = f"{url}: {exc or type(exc).__name__}"
            continue
        return found
    return "unreachable", f"This node could not reach its own entrance ({failure})."


async def check(fresh: bool = False, cookies: str = "") -> dict[str, Any]:
    """Return the state of the proxy check: `{"state", "detail", "checked"}`.

    Args:
        cookies: The `Cookie` header of the request at hand, where a proxy
            signed it in; kept for the checks that follow.
        fresh: Ask the entrance now rather than return a kept result, and
            count of the forged headers browsers sent only those of the last
            `FRESH_SECONDS`, which is when the web app's test sent its own.

    Returns:
        `state` is `off` when no header signs users in, else `guarded`,
        `open`, or `unreachable`.
    """
    global _renewal, _cookies
    if not enabled():
        return {"state": "off", "detail": "", "checked": 0.0}
    # A check made with no sign-in at hand says less: the first request that brings one asks again.
    again = fresh or (bool(cookies) and not _cookies)
    _cookies = cookies or _cookies
    forged = _recent(FRESH_SECONDS if fresh else FORGED_SECONDS)
    if fresh:
        _forged.clear()
        _forged.update(dict.fromkeys(forged, time.time()))
    if forged:
        return {"state": "open", "detail": _passes_on(forged), "checked": max(_forged.values())}
    key = _settings_key()
    kept = _result if _result.get("key") == key else None
    if kept and not again:
        age = time.time() - kept["checked"]
        if kept["state"] == "guarded":
            # A request is not held up to confirm what held a minute ago.
            if age > GUARDED_SECONDS and (not _renewal or _renewal.done()):
                _renewal = asyncio.create_task(_renew())
            return _view()
        if age <= RETRY_SECONDS:
            return _view()
    asked = time.time()
    async with _lock:
        if _result.get("key") != key or _result["checked"] < asked:
            await _store(key)
    return _view()


def _view() -> dict[str, Any]:
    """Return the kept result as callers see it."""
    return {name: _result[name] for name in ("state", "detail", "checked")}


async def _store(key: tuple[str, ...]) -> None:
    """Run the check and keep its result, logging a change of state."""
    found, detail = await _probe(_cookies)
    if found != _result.get("state") or key != _result.get("key"):
        log = logger.info if found == "guarded" else logger.error
        log("Reverse proxy sign-in check: %s. %s", found, detail)
    _result.clear()
    _result.update(key=key, state=found, detail=detail, checked=time.time())


async def _renew() -> None:
    """Renew a kept result in the background; a renewal that fails leaves the next request to ask."""
    try:
        async with _lock:
            await _store(_settings_key())
    except Exception:  # noqa: BLE001 - nobody awaits this task
        logger.exception("The reverse proxy sign-in check could not be renewed.")


async def believed(address: str, cookies: str = "") -> bool:
    """Whether the sign-in headers of a request that arrived from `address` with `cookies` count."""
    if not enabled() or not cluster.trusted_proxy(address) or cluster.session_address(address):
        return False
    if settings.proxy_auth_unchecked:
        return True
    return (await check(cookies=cookies))["state"] == "guarded"
