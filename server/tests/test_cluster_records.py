"""A cluster record is named inside the cluster's records, whatever name a route was handed."""

import asyncio
import os

import pytest
from fastapi import HTTPException

from app import cluster
from app.routers import cluster_admin
from app.settings import settings


def test_a_pool_name_reaches_nothing_outside_the_pools(tmp_path, monkeypatch):
    """Deleting a pool named with `..` refuses it and removes no record elsewhere."""
    monkeypatch.setattr(settings, "cluster_path", str(tmp_path / "cluster"))
    os.makedirs(tmp_path / "cluster" / "pools")
    outside = tmp_path / "cluster" / "settings.yml"
    outside.write_text("kept: true\n")
    with pytest.raises(HTTPException) as refused:
        asyncio.run(cluster_admin.delete_pool("../settings"))
    assert refused.value.status_code == 400
    assert outside.exists()


def test_record_files_stay_inside_the_cluster_records(tmp_path, monkeypatch):
    """Each record file is in its folder of the records, and a name that would leave it is refused."""
    monkeypatch.setattr(settings, "cluster_path", str(tmp_path))
    base = os.path.realpath(tmp_path)
    assert cluster.pool_file("gpu") == os.path.join(base, "pools", "gpu.yml")
    assert cluster.homes_file("alice") == os.path.join(base, "homes", "alice.yml")
    for record_file in (cluster.node_file, cluster.pool_file, cluster.join_file, cluster.homes_file):
        with pytest.raises(ValueError):
            record_file("../../escape")
