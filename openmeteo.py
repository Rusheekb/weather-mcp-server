"""Client for the Open-Meteo weather API (https://open-meteo.com).

This module knows nothing about MCP: it fetches weather data and returns plain
Python objects. server.py turns those into MCP tools.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

Units = Literal["fahrenheit", "celsius"]

# How long cached answers stay fresh, in seconds
GEOCODE_TTL = 24 * 60 * 60   # a city doesn't move
CURRENT_TTL = 10 * 60        # Open-Meteo refreshes current conditions every 15 minutes
FORECAST_TTL = 30 * 60

# Open-Meteo reports conditions as numeric WMO weather codes; map them to words
WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def describe_weather_code(code: int) -> str:
    return WEATHER_CODES.get(code, "unknown conditions")


def unit_labels(units: Units) -> tuple[str, str]:
    """(temperature unit, wind speed unit) as shown to users."""
    return ("°F", "mph") if units == "fahrenheit" else ("°C", "km/h")


@dataclass(frozen=True)
class Location:
    name: str          # e.g. "Chicago, Illinois, United States"
    latitude: float
    longitude: float


class TTLCache:
    """A dictionary whose entries expire after a fixed number of seconds."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.clock = clock  # injectable so tests can fast-forward time
        self._entries: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> tuple[bool, Any]:
        """Returns (found, value). A cached None is still a hit."""
        entry = self._entries.get(key)
        if entry is None:
            return False, None
        expires_at, value = entry
        if self.clock() >= expires_at:
            del self._entries[key]
            return False, None
        return True, value

    def set(self, key: Any, value: Any) -> None:
        self._entries[key] = (self.clock() + self.ttl, value)


class WeatherClient:
    """Fetches weather from Open-Meteo, caching answers to avoid repeat API calls.

    Raises httpx.HTTPError if the API can't be reached or returns an error.
    """

    def __init__(self, http: httpx.AsyncClient | None = None, clock: Callable[[], float] = time.monotonic):
        # Tests pass in an http client backed by a fake API
        self.http = http or httpx.AsyncClient(timeout=10)
        self._locations = TTLCache(GEOCODE_TTL, clock)
        self._current = TTLCache(CURRENT_TTL, clock)
        self._forecasts = TTLCache(FORECAST_TTL, clock)

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await self.http.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def find_location(self, city: str) -> Location | None:
        """Look up a city by name. Returns None if no city matches."""
        key = city.strip().lower()
        found, location = self._locations.get(key)
        if found:
            return location

        data = await self._get_json(GEOCODING_URL, {"name": city, "count": 1})
        results = data.get("results")
        location = None
        if results:
            first = results[0]
            # Region and country can be missing for some places
            parts = [first["name"], first.get("admin1"), first.get("country")]
            location = Location(
                name=", ".join(part for part in parts if part),
                latitude=first["latitude"],
                longitude=first["longitude"],
            )
        self._locations.set(key, location)
        return location

    def _unit_params(self, location: Location, units: Units) -> dict[str, Any]:
        return {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "temperature_unit": units,
            "wind_speed_unit": "mph" if units == "fahrenheit" else "kmh",
        }

    async def current(self, location: Location, units: Units = "fahrenheit") -> dict[str, Any]:
        """Current conditions: temperature_2m, relative_humidity_2m, weather_code, wind_speed_10m."""
        key = (location.latitude, location.longitude, units)
        found, current = self._current.get(key)
        if found:
            return current

        params = self._unit_params(location, units)
        params["current"] = "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        current = (await self._get_json(FORECAST_URL, params))["current"]
        self._current.set(key, current)
        return current

    async def forecast(self, location: Location, days: int, units: Units = "fahrenheit") -> list[dict[str, Any]]:
        """One dict per day: date, weather_code, high, low, precipitation_chance (may be None).

        Can return fewer than `days` entries when the furthest days have no data yet.
        """
        key = (location.latitude, location.longitude, units, days)
        found, forecast = self._forecasts.get(key)
        if found:
            return forecast

        params = self._unit_params(location, units)
        params["daily"] = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        params["forecast_days"] = days
        params["timezone"] = "auto"  # dates in the city's local time, not GMT
        daily = (await self._get_json(FORECAST_URL, params))["daily"]

        # The API returns parallel lists (all dates, all highs, ...); turn them into one dict per day.
        # The furthest-out days can come back as nulls before the model has run, so skip those.
        forecast = [
            {"date": date, "weather_code": code, "high": high, "low": low, "precipitation_chance": rain}
            for date, code, high, low, rain in zip(
                daily["time"],
                daily["weather_code"],
                daily["temperature_2m_max"],
                daily["temperature_2m_min"],
                daily["precipitation_probability_max"],
            )
            if high is not None and low is not None
        ]
        self._forecasts.set(key, forecast)
        return forecast
