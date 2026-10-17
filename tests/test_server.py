"""End-to-end tests: a real MCP client talks to the server over the protocol, backed by the fake API."""

import pytest
from mcp import Client

import server

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(weather, monkeypatch):
    monkeypatch.setattr(server, "weather", weather)  # swap the live API for the fake one
    async with Client(server.mcp) as client:
        yield client


async def call(client, tool, **arguments):
    result = await client.call_tool(tool, arguments)
    return result.is_error, result.content[0].text


async def test_lists_both_tools_with_schemas(client):
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"get_current_weather", "get_forecast"}

    days = tools["get_forecast"].input_schema["properties"]["days"]
    assert (days["minimum"], days["maximum"], days["default"]) == (1, 16, 3)
    units = tools["get_current_weather"].input_schema["properties"]["units"]
    assert units["enum"] == ["fahrenheit", "celsius"]
    assert tools["get_current_weather"].input_schema["required"] == ["city"]

    for tool in tools.values():
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True


async def test_current_weather(client):
    assert await call(client, "get_current_weather", city="Chicago") == (
        False, "Chicago, Illinois, United States: 62.9°F, overcast, 90% humidity, wind 8.6 mph")


async def test_current_weather_celsius_labels(client):
    _, text = await call(client, "get_current_weather", city="Chicago", units="celsius")
    assert "°C" in text and "km/h" in text


async def test_forecast(client):
    assert await call(client, "get_forecast", city="Chicago", days=2) == (False, "\n".join([
        "2-day forecast for Chicago, Illinois, United States:",
        "- Thu Oct 15: high 70.1°F, low 50.2°F, clear sky, 5% chance of precipitation",
        "- Fri Oct 16: high 65.0°F, low 48.5°F, light rain, 80% chance of precipitation",
    ]))


async def test_forecast_says_when_days_are_missing(client):
    _, text = await call(client, "get_forecast", city="Chicago", days=3)
    assert text.splitlines()[0] == (
        "2-day forecast for Chicago, Illinois, United States: (only 2 of the 3 requested days have data yet)")


async def test_unknown_city(client):
    for tool in ("get_current_weather", "get_forecast"):
        assert await call(client, tool, city="Xyzzyqqq") == (False, "Couldn't find a city named Xyzzyqqq")


async def test_api_outage_becomes_a_message(client, fake_api):
    fake_api.fail_with = 503
    is_error, text = await call(client, "get_current_weather", city="Chicago")
    assert not is_error
    assert text.startswith("Weather service error: Server error '503")


async def test_out_of_range_days_rejected_by_schema(client, fake_api):
    is_error, text = await call(client, "get_forecast", city="Chicago", days=30)
    assert is_error
    assert "less than or equal to 16" in text
    assert fake_api.requests == []  # rejected before any API call
