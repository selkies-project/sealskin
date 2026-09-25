"""Password hashing and upload id validation."""

import pytest
from fastapi import HTTPException

from app.routers.uploads import upload_path, validate_upload_id
from app.security import hash_share_password, verify_share_password
from app.settings import settings


def test_scrypt_hash_roundtrip_and_legacy_rejected():
    stored = hash_share_password("hunter2")
    assert stored.startswith("scrypt$")
    assert verify_share_password("hunter2", stored)
    assert not verify_share_password("wrong", stored)
    legacy = "e0c9035898dd52fc65c41454cec9c4d2611bfb37" + "0" * 24
    assert not verify_share_password("old", legacy)
    assert not verify_share_password("x", "")


def test_upload_id_validation_and_user_scoping():
    good = "123e4567-e89b-12d3-a456-426614174000"
    assert validate_upload_id(good) == good
    for bad in ("", "../etc", "123E4567-E89B-12D3-A456-426614174000", "abc"):
        with pytest.raises(HTTPException):
            validate_upload_id(bad)
    path = upload_path("alice", good)
    assert path.startswith(f"{settings.upload_dir}/alice/")
    with pytest.raises(HTTPException):
        upload_path("../bob", good)


def test_host_port_brackets_ipv6():
    from app.providers.base_provider import host_port

    assert host_port("10.0.0.5", 3000) == "10.0.0.5:3000"
    assert host_port("fd00:10:244::b", 3000) == "[fd00:10:244::b]:3000"


@pytest.mark.parametrize(
    ("host_url", "expected"),
    [
        ("sealskin.example.com", ("sealskin.example.com", None)),
        ("sealskin.example.com:443", ("sealskin.example.com", 443)),
        ("[2001:db8::1]:8443", ("[2001:db8::1]", 8443)),
        ("2001:db8::1", ("2001:db8::1", None)),
    ],
)
def test_external_address_splits_a_port_off_host_url(monkeypatch, host_url, expected):
    from app import user_manager

    monkeypatch.setenv("HOST_URL", host_url)
    assert user_manager.external_address() == expected
