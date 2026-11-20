"""The UI is served locally, with security headers, and follows the CLAUDE.md §8 safety rules."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from algorythm5.config import Settings
from algorythm5.web.app import STATIC_DIR, create_app

ASSETS = sorted(p for p in STATIC_DIR.rglob("*") if p.is_file())


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_index_references_only_local_assets(client: TestClient) -> None:
    page = client.get("/").text
    assert 'href="/static/css/tokens.css"' in page
    assert 'src="/static/js/main.js"' in page
    assert not re.search(r"(src|href)=\"https?://", page)


@pytest.mark.parametrize(
    ("path", "media"),
    [
        ("/static/css/app.css", "text/css"),
        ("/static/js/main.js", "javascript"),
        ("/static/js/views/results.js", "javascript"),
    ],
)
def test_assets_are_served_with_headers(client: TestClient, path: str, media: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert media in response.headers["content-type"]
    assert "script-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("asset", ASSETS, ids=lambda p: p.name)
def test_assets_never_use_inner_html_or_remote_resources(asset: Path) -> None:
    text = asset.read_text(encoding="utf-8")
    assert "innerHTML" not in text
    assert "outerHTML" not in text
    assert not re.search(r"https?://(?!www\.w3\.org)", text), asset


def test_views_do_not_call_fetch_directly() -> None:
    for view in (STATIC_DIR / "js" / "views").glob("*.js"):
        assert "fetch(" not in view.read_text(encoding="utf-8"), view.name


def test_dark_mode_tokens_cover_both_paths() -> None:
    tokens = (STATIC_DIR / "css" / "tokens.css").read_text(encoding="utf-8")
    assert "@media (prefers-color-scheme: dark)" in tokens
    assert ':root:not([data-theme="light"])' in tokens
    assert ':root[data-theme="dark"]' in tokens


def test_components_use_tokens_not_raw_colors() -> None:
    css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "rgb(" not in css
