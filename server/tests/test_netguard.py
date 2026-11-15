"""The guarded fetch: public addresses only for a user's address, at every hop, and bounded."""

import httpx
import pytest

from app import netguard


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.9", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.1.1", "::1", "fe80::1", "fd00::1", "::ffff:10.0.0.1", "not-an-address"],
)
def test_addresses_off_the_public_internet_are_not_public(address):
    assert not netguard.is_public(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_public(address):
    assert netguard.is_public(address)


@pytest.fixture
def web(monkeypatch):
    """Answer fetches from a table of handlers, and resolve names from a table of addresses."""
    names = {"public.example": ["93.184.216.34"], "inside.example": ["10.0.0.5"], "mixed.example": ["93.184.216.34", "127.0.0.1"]}
    seen = []

    async def resolve(host, port):
        return names.get(host, [])

    def handler(request):
        seen.append(request)
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "http://inside.example/secret"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 5000)
        return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain; charset=utf-8"})

    monkeypatch.setattr(netguard, "_resolve", resolve)
    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(handler))
    return seen


async def test_a_public_host_is_fetched_at_the_address_that_was_checked(web):
    fetched = await netguard.fetch("https://public.example/feed", public_only=True)
    assert (fetched.status, fetched.content, fetched.media_type) == (200, b"hello", "text/plain")
    assert web[0].url.host == "93.184.216.34" and web[0].headers["host"] == "public.example"
    assert web[0].extensions["sni_hostname"] == "public.example"


@pytest.mark.parametrize("url", ["http://inside.example/", "http://mixed.example/", "http://unknown.example/", "http://public.example/hop"])
async def test_a_host_with_an_inside_address_is_refused_at_any_hop(web, url):
    with pytest.raises(netguard.Refused):
        await netguard.fetch(url, public_only=True)
    assert not any(request.url.path == "/secret" for request in web)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://public.example/", "http://user:pw@public.example/", "gopher://public.example/"])
async def test_only_plain_web_addresses_are_fetched(web, url):
    with pytest.raises(netguard.Refused):
        await netguard.fetch(url, public_only=False)


async def test_an_administrators_address_may_be_inside_and_a_long_answer_is_refused(web):
    assert (await netguard.fetch("http://inside.example/status", public_only=False)).content == b"hello"
    with pytest.raises(netguard.Refused):
        await netguard.fetch("http://inside.example/big", public_only=False, max_bytes=1000)
