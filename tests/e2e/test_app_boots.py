"""End-to-end smoke test: the Flask app boots and its routes respond."""
import importlib
import json
import os
import sys

import pytest

pytestmark = pytest.mark.e2e


def test_app_boots_and_lists_services(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.mkdir("services")
    open(os.path.join("services", ".gitkeep"), "w").close()
    with open(os.path.join("services", "api-keys.json"), "w") as f:
        json.dump(["e2e-key"], f)

    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")

    # the module-level setup created the services database
    assert os.path.exists(os.path.join("services", "services.db"))

    client = app_module.app.test_client()
    resp = client.get("/service")
    assert resp.status_code == 200
    assert resp.get_json() == []
