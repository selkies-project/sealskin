"""Password hashing and upload id validation."""

import pytest
from fastapi import HTTPException

from app.routers.uploads import upload_path, validate_upload_id
from app.security import hash_share_password, verify_share_password
from app.settings import settings


def test_scrypt_hash_roundtrip_and_legacy():
    stored = hash_share_password("hunter2")
    assert stored.startswith("scrypt$")
    assert verify_share_password("hunter2", stored)
    assert not verify_share_password("wrong", stored)
    import hashlib

    legacy = hashlib.sha256(b"old").hexdigest()
    assert verify_share_password("old", legacy)
    assert not verify_share_password("new", legacy)
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
