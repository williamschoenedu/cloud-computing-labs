"""Smoke tests against the deployed Weather Advisor application."""

import os

import pytest
import requests

from stack.config import load_dotenv
from stack.weather_config import load_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="Requires explicitly enabled live application tests.",
    ),
]


@pytest.fixture
def urls():
    """Read deployed endpoints without accessing any secret."""
    v = load_settings().values
    host = load_dotenv("stack_outputs.env")["ALB_DNS_NAME"]
    return (
        f'http://{host}:{v["WEATHER_LISTENER_PORT"]}',
        f'http://{host}:{v["ADVISOR_LISTENER_PORT"]}',
    )


def test_weather_cache_and_history(urls):
    """Verify a forecast, a subsequent cache hit and persisted history."""
    weather, _ = urls
    first = requests.get(
        weather + "/weather", params={"zip": "60616"}, timeout=40
    )
    assert first.status_code == 200
    data = first.json()
    assert data["city"] == "Chicago"
    assert data["forecast"]["temperatureUnit"] == "F"
    assert data["cache"] in ("miss", "hit")
    second = requests.get(
        weather + "/weather", params={"zip": "60616"}, timeout=15
    )
    assert second.status_code == 200
    assert second.json()["cache"] == "hit"
    history = requests.get(weather + "/history", timeout=15)
    assert history.status_code == 200
    assert any(row["zip"] == "60616" for row in history.json()["lookups"])


@pytest.mark.parametrize("zip_code,status", [("abc", 400), ("00000", 404)])
def test_invalid_and_unknown_zip(urls, zip_code, status):
    """Reject malformed ZIP codes and report unknown ZIP codes."""
    response = requests.get(
        urls[0] + "/weather", params={"zip": zip_code}, timeout=30
    )
    assert response.status_code == status


def test_advisor_recommendation(urls):
    """Return an explicitly identified Gemini or fallback recommendation."""
    response = requests.get(
        urls[1] + "/advice", params={"zip": "60616"}, timeout=45
    )
    assert response.status_code == 200
    data = response.json()
    assert data["advice"].strip()
    assert data["source"] in ("gemini", "fallback")
    assert data["zip"] == "60616"


def test_browser_page(urls):
    """Serve the Weather Advisor HTML interface."""
    response = requests.get(urls[1] + "/", timeout=15)
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]
    assert "Weather Advisor" in response.text
