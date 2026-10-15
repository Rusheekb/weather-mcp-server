"""Shared test fixtures: a fake Open-Meteo API so tests never touch the network."""

import httpx
import pytest

from openmeteo import FORECAST_URL, GEOCODING_URL, WeatherClient

CHICAGO = {"name": "Chicago", "admin1": "Illinois", "country": "United States",
           "latitude": 41.85, "longitude": -87.65}

CURRENT = {"time": "2026-10-15T12:00", "interval": 900, "temperature_2m": 62.9,
           "relative_humidity_2m": 90, "weather_code": 3, "wind_speed_10m": 8.6}

DAILY = {
    "time": ["2026-10-15", "2026-10-16", "2026-10-17"],
    "weather_code": [0, 61, None],
    "temperature_2m_max": [70.1, 65.0, None],   # third day has no data yet
    "temperature_2m_min": [50.2, 48.5, None],
    "precipitation_probability_max": [5, 80, None],
}


class FakeOpenMeteo:
    """Answers geocoding and forecast requests with canned data and records every request."""

    def __init__(self):
        self.places = {"chicago": CHICAGO}
        self.fail_with: int | None = None  # set to e.g. 503 to simulate an outage
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with:
            return httpx.Response(self.fail_with)

        url = str(request.url.copy_with(query=None))
        if url == GEOCODING_URL:
            place = self.places.get(request.url.params["name"].lower())
            # Like the real API, a miss has no "results" key at all
            return httpx.Response(200, json={"results": [place]} if place else {"generationtime_ms": 0.1})
        if url == FORECAST_URL:
            if "current" in request.url.params:
                return httpx.Response(200, json={"current": CURRENT})
            days = int(request.url.params["forecast_days"])
            return httpx.Response(200, json={"daily": {key: values[:days] for key, values in DAILY.items()}})
        return httpx.Response(404)


class FakeClock:
    """A clock tests can move forward by hand, to check cache expiry without waiting."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_api():
    return FakeOpenMeteo()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def weather(fake_api, clock):
    """A WeatherClient wired to the fake API and the fake clock."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake_api.handle))
    return WeatherClient(http=http, clock=clock)
