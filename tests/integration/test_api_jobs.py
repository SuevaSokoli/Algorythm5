"""End-to-end API tests through FastAPI's TestClient with a temporary DATA_DIR."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from algorythm5.config import Settings
from algorythm5.services.jobs import JobService, display_filename, download_stem
from algorythm5.web.app import create_app

FIXED_NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
CSV = """Emri,Mbiemri,Email,Tel,Qyteti,Abonuar
ANA,HOXHA,ANA.HOXHA@GMIAL.COM,0692345678,TIRANË,po
Ana,Hoxha,ana.hoxha@gmail.com,069 234 5678,Tiranë,
BEN,KOLA,,3.55692E+11,DURRËS,
Klea,Muça,klea.muca@gmail.com,,Korçë,po
""".encode()


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", **overrides)  # type: ignore[call-arg]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(make_settings(tmp_path), clock=lambda: FIXED_NOW)
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, data: bytes = CSV, name: str = "klientet.csv") -> Any:
    return client.post("/api/jobs", files={"file": (name, data, "application/octet-stream")})


def completed_job(client: TestClient) -> str:
    job_id: str = upload(client).json()["job_id"]
    assert (
        client.put(
            f"/api/jobs/{job_id}/mapping",
            json={"columns": [{"index": 0, "target_field": "first_name"}]},
        ).status_code
        == 200
    )
    assert client.post(f"/api/jobs/{job_id}/run").status_code == 202
    return job_id


def test_health_rules_and_security_headers(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.json() == {"status": "ok", "ruleset_version": "1.0.0"}
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    codes = {r["code"] for r in client.get("/api/rules").json()}
    assert {"PHN-002", "DUP-001", "ROW-002"} <= codes


def test_frontend_is_served_without_external_docs(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert client.get("/docs").status_code == 404


def test_full_flow(client: TestClient) -> None:
    created = upload(client)
    assert created.status_code == 201
    body = created.json()
    job_id = body["job_id"]
    assert [c["target_field"] for c in body["columns"]] == [
        "first_name", "last_name", "email", "phone", "city", "consent",
    ]  # fmt: skip
    assert body["columns"][0]["samples"] == ["ANA", "Ana", "BEN"]
    assert len(body["preview"]) == 4
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "uploaded"

    assert client.post(f"/api/jobs/{job_id}/run").status_code == 409  # not mapped yet
    mapped = client.put(
        f"/api/jobs/{job_id}/mapping", json={"columns": [{"index": 4, "target_field": "city"}]}
    )
    assert mapped.status_code == 200
    assert client.post(f"/api/jobs/{job_id}/run").json() == {"status": "processing"}

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "completed"
    assert job["counts"] == {"FIXED": 1, "MERGED": 1, "REJECTED": 1, "OK": 1}
    assert (job["rows_in"], job["rows_out"]) == (4, 2)
    assert client.post(f"/api/jobs/{job_id}/run").status_code == 409  # cannot run twice

    summary = client.get(f"/api/jobs/{job_id}/summary").json()
    assert summary["rows_out"] == 2 and summary["ruleset_version"] == "1.0.0"
    assert summary["top_rules"][0]["explanation"]

    changes = client.get(f"/api/jobs/{job_id}/changes", params={"page_size": 2}).json()
    assert changes["page_size"] == 2 and len(changes["items"]) == 2 and changes["total"] > 2
    filtered = client.get(f"/api/jobs/{job_id}/changes", params={"rule_code": "EML-003"}).json()
    assert [c["new_value"] for c in filtered["items"]] == ["ana.hoxha@gmail.com"]

    review = client.get(f"/api/jobs/{job_id}/review").json()
    codes = {i["rule_code"] for i in review["items"]}
    assert {"PHN-002", "ROW-001"} <= codes
    damaged = next(i for i in review["items"] if i["rule_code"] == "PHN-002")
    assert damaged["original_value"] == "3.55692E+11" and damaged["status"] == "REJECTED"
    blocking = client.get(f"/api/jobs/{job_id}/review", params={"severity": "blocking"}).json()
    assert {i["severity"] for i in blocking["items"]} == {"blocking"}

    report = client.get(f"/api/jobs/{job_id}/files/report_xlsx")
    assert report.status_code == 200
    assert "klientet_A5_" in report.headers["content-disposition"]
    sheets = load_workbook(io.BytesIO(report.content)).sheetnames
    assert sheets == ["Summary", "Clean", "Review", "Changes", "Suppressed", "Rejected"]
    clean = client.get(f"/api/jobs/{job_id}/files/clean_csv")
    assert clean.content.startswith(b"\xef\xbb\xbf")
    assert "Tiranë" in clean.content.decode("utf-8-sig")
    assert client.get(f"/api/jobs/{job_id}/files/review_csv").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/files/changes_csv").status_code == 200

    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_xlsx_upload(client: TestClient) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Email", "Mobile"])
    sheet.append(["ana@gmail.com", 692345678])
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = upload(client, buffer.getvalue(), "export.xlsx")
    assert response.status_code == 201
    assert response.json()["file_kind"] == "xlsx"


@pytest.mark.parametrize(
    ("data", "name", "status", "code"),
    [
        (b"hello", "notes.txt", 415, "UNSUPPORTED_TYPE"),
        (b"PK\x03\x04fake", "customers.csv", 415, "ING-901"),  # zip content, csv extension
        (b"Email\na@b.al\n", "customers.xlsx", 415, "ING-901"),  # text content, xlsx extension
        (b"", "empty.csv", 415, "ING-901"),
        (b"\x00\x01", "bin.csv", 415, "ING-901"),
    ],
)
def test_rejected_uploads(
    client: TestClient, data: bytes, name: str, status: int, code: str
) -> None:
    response = upload(client, data, name)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_upload_size_limit(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, max_upload_mb=1), clock=lambda: FIXED_NOW)
    with TestClient(app) as client:
        response = upload(client, b"Email\n" + b"a@b.al\n" * 200_000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "ING-902"


def test_row_limit(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path, max_rows=2), clock=lambda: FIXED_NOW)
    with TestClient(app) as client:
        response = upload(client)
    assert (response.status_code, response.json()["error"]["code"]) == (413, "ING-902")


def test_mapping_errors(client: TestClient) -> None:
    job_id = upload(client).json()["job_id"]
    url = f"/api/jobs/{job_id}/mapping"
    no_contact = {
        "columns": [{"index": 2, "target_field": None}, {"index": 3, "target_field": None}]
    }
    assert client.put(url, json=no_contact).json()["error"]["code"] == "MAP-901"
    twice = {"columns": [{"index": 0, "target_field": "city"}]}
    assert client.put(url, json=twice).json()["error"]["code"] == "INVALID_MAPPING"
    unknown = {"columns": [{"index": 0, "target_field": "shoe_size"}]}
    assert client.put(url, json=unknown).status_code == 422
    missing = {"columns": [{"index": 99, "target_field": None}]}
    assert client.put(url, json=missing).json()["error"]["code"] == "INVALID_MAPPING"
    extra = {"columns": [{"index": 0, "target_field": None, "evil": 1}]}
    assert client.put(url, json=extra).json()["error"]["code"] == "VALIDATION"
    assert client.put(url, json={"columns": []}).status_code == 422


def test_manual_mapping_is_recorded(client: TestClient) -> None:
    job_id = upload(client).json()["job_id"]
    body = {"columns": [{"index": 5, "target_field": None}]}
    columns = client.put(f"/api/jobs/{job_id}/mapping", json=body).json()["columns"]
    assert (columns[5]["target_field"], columns[5]["method"]) == (None, "manual")
    assert columns[0]["method"] == "auto"


def test_results_not_ready_and_unknown_jobs(client: TestClient) -> None:
    job_id = upload(client).json()["job_id"]
    for path in ("summary", "changes", "review", "files/clean_csv"):
        response = client.get(f"/api/jobs/{job_id}/{path}")
        assert (response.status_code, response.json()["error"]["code"]) == (409, "JOB_STATE")
    missing = str(uuid.uuid4())
    assert client.get(f"/api/jobs/{missing}").json()["error"]["code"] == "JOB_NOT_FOUND"
    assert client.delete(f"/api/jobs/{missing}").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/jobs/not-a-uuid",
        "/api/jobs/..%2F..%2Fetc%2Fpasswd",
        "/api/jobs/6ba7b810-9dad-11d1-80b4-00c04fd430c8",  # UUID1, not UUID4
    ],
)
def test_path_traversal_and_bad_ids(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code in (404, 422)
    assert "error" in response.json()


def test_invalid_query_and_kind(client: TestClient) -> None:
    job_id = completed_job(client)
    assert client.get(f"/api/jobs/{job_id}/files/secrets").status_code == 422
    assert client.get(f"/api/jobs/{job_id}/changes", params={"page_size": 500}).status_code == 422
    assert (
        client.get(f"/api/jobs/{job_id}/changes", params={"rule_code": "x'--"}).status_code == 422
    )
    assert client.get(f"/api/jobs/{job_id}/review", params={"severity": "info"}).status_code == 422


def test_unknown_route_and_method(client: TestClient) -> None:
    assert client.get("/api/nope").json()["error"]["code"] == "HTTP_404"
    assert client.patch("/api/health").json()["error"]["code"] == "HTTP_405"


def test_unexpected_errors_are_generic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("C:\\secret\\path leaked?")

    monkeypatch.setattr(JobService, "get_job", explode)
    app = create_app(make_settings(tmp_path), clock=lambda: FIXED_NOW)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/jobs/{uuid.uuid4()}")
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL",
            "message": "Something went wrong on our side. Please try again.",
        }
    }


def test_failed_run_marks_job_failed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = upload(client).json()["job_id"]
    client.put(
        f"/api/jobs/{job_id}/mapping",
        json={"columns": [{"index": 0, "target_field": "first_name"}]},
    )

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("algorythm5.services.jobs.run_pipeline", broken)
    client.post(f"/api/jobs/{job_id}/run")
    job = client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["error_code"]) == ("failed", "INTERNAL")
    assert job["error_message"].startswith("Something went wrong")


def test_retention_purges_on_startup(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, clock=lambda: FIXED_NOW)) as client:
        job_id = upload(client).json()["job_id"]
    later = FIXED_NOW + timedelta(days=8)
    with TestClient(create_app(settings, clock=lambda: later)) as client:
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert not list((tmp_path / "data" / "uploads").glob("*"))


@pytest.mark.parametrize(
    ("raw", "display", "stem"),
    [
        ("C:\\Users\\x\\Klientët 2024.csv", "Klientët 2024.csv", "Klient_t_2024"),
        ("../../etc/passwd.csv", "passwd.csv", "passwd"),
        ("bad\x00name.xlsx", "badname.xlsx", "badname"),
        ("", "upload", "upload"),
        ("....csv", "....csv", "export"),
    ],
)
def test_filename_sanitising(raw: str, display: str, stem: str) -> None:
    assert display_filename(raw) == display
    assert download_stem(raw) == stem
