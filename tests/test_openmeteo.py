"""Tests for the Open-Meteo API layer (no MCP involved)."""

import httpx
import pytest

from openmeteo import CURRENT_TTL, Location, TTLCache, describe_weather_code

pytestmark = pytest.mark.anyio

CHICAGO = Location("Chicago, Illinois, United States", 41.85, -87.65)


async def test_find_location_builds_full_name(weather):
    assert await weather.find_location("Chicago") == CHICAGO


async def test_find_location_skips_missing_region(weather, fake_api):
    fake_api.places["monaco"] = {"name": "Monaco", "country": "Monaco", "latitude": 43.7, "longitude": 7.4}
    location = await weather.find_location("Monaco")
    assert location.name == "Monaco, Monaco"


async def test_find_location_unknown_city_returns_none(weather):
    assert await weather.find_location("Xyzzyqqq") is None


async def test_find_location_is_cached_case_insensitively(weather, fake_api):
    await weather.find_location("Chicago")
    await weather.find_location("  chicago ")
    assert len(fake_api.requests) == 1


async def test_unknown_city_is_cached_too(weather, fake_api):
    await weather.find_location("Xyzzyqqq")
    await weather.find_location("Xyzzyqqq")
    assert len(fake_api.requests) == 1


async def test_current_sends_units_and_returns_conditions(weather, fake_api):
    current = await weather.current(CHICAGO, "celsius")
    assert current["temperature_2m"] == 62.9
    params = fake_api.requests[0].url.params
    assert params["temperature_unit"] == "celsius"
    assert params["wind_speed_unit"] == "kmh"


async def test_current_cache_expires(weather, fake_api, clock):
    await weather.current(CHICAGO)
    await weather.current(CHICAGO)
    assert len(fake_api.requests) == 1  # second call served from cache

    clock.now += CURRENT_TTL
    await weather.current(CHICAGO)
    assert len(fake_api.requests) == 2  # stale entry refetched


async def test_current_cache_is_per_units(weather, fake_api):
    await weather.current(CHICAGO, "fahrenheit")
    await weather.current(CHICAGO, "celsius")
    assert len(fake_api.requests) == 2


async def test_forecast_turns_columns_into_days(weather, fake_api):
    forecast = await weather.forecast(CHICAGO, days=2)
    assert forecast == [
        {"date": "2026-10-15", "weather_code": 0, "high": 70.1, "low": 50.2, "precipitation_chance": 5},
        {"date": "2026-10-16", "weather_code": 61, "high": 65.0, "low": 48.5, "precipitation_chance": 80},
    ]
    params = fake_api.requests[0].url.params
    assert params["forecast_days"] == "2"
    assert params["timezone"] == "auto"


async def test_forecast_skips_days_without_data(weather):
    forecast = await weather.forecast(CHICAGO, days=3)
    assert [day["date"] for day in forecast] == ["2026-10-15", "2026-10-16"]


async def test_api_error_raises(weather, fake_api):
    fake_api.fail_with = 503
    with pytest.raises(httpx.HTTPStatusError):
        await weather.find_location("Chicago")


def test_ttl_cache_hit_miss_and_expiry(clock):
    cache = TTLCache(ttl=60, clock=clock)
    assert cache.get("k") == (False, None)
    cache.set("k", None)
    assert cache.get("k") == (True, None)  # a cached None is still a hit
    clock.now = 60
    assert cache.get("k") == (False, None)


def test_describe_weather_code():
    assert describe_weather_code(0) == "clear sky"
    assert describe_weather_code(12345) == "unknown conditions"
