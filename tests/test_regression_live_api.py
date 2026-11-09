from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_health_still_works():
    response = client.get("/health")
    assert response.status_code == 200


def test_manufacturing_page_and_catalog_endpoints():
    page = client.get("/manufacturing")
    assert page.status_code == 200
    assert "Manufacturing Data" in page.text or "AAS" in page.text

    catalog = client.get("/ui/api/manufacturing/catalog")
    assert catalog.status_code == 200
    body = catalog.json()
    assert "items" in body


def test_existing_datasets_page_untouched():
    response = client.get("/datasets")
    assert response.status_code == 200


def test_manufacturing_upload_preview_flow():
    fixtures = ROOT / "tests" / "fixtures" / "sample_bms.json"
    with fixtures.open("rb") as handle:
        response = client.post(
            "/ui/api/manufacturing/pipeline/upload-preview",
            files={"file": ("sample_bms.json", handle, "application/json")},
            data={"dataset_id": "auto-ev-bms-v1", "robot_mode": "never"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "draft"
    assert payload["aas_validation"]["ok"] is True

    denied = client.post(
        "/ui/api/manufacturing/pipeline/register",
        json={"draft_id": payload["draft_id"], "confirm": False},
    )
    assert denied.status_code == 400

    registered = client.post(
        "/ui/api/manufacturing/pipeline/register",
        json={"draft_id": payload["draft_id"], "confirm": True},
    )
    assert registered.status_code == 200
    assert registered.json()["status"] == "registered"
