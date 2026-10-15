"""Weather MCP server: exposes live weather from Open-Meteo as MCP tools.

Run over stdio (how Claude Code and the MCP Inspector launch it):
    python server.py
Or as an HTTP server at http://127.0.0.1:8000/mcp:
    python server.py --http
"""

import argparse
from datetime import date
from typing import Annotated

import httpx
from mcp.server import MCPServer
from pydantic import Field

from openmeteo import Units, WeatherClient, describe_weather_code, unit_labels

mcp = MCPServer(
    "weather",
    instructions="Live weather for any city in the world: current conditions and daily forecasts up to 16 days.",
)

# One shared client for the life of the server, so its cache and connections are reused
weather = WeatherClient()

# Parameter types shared by both tools; the descriptions show up in the tool schema the model reads
City = Annotated[str, Field(description='City name, e.g. "Chicago" or "Tokyo"')]
UnitsParam = Annotated[Units, Field(description="Temperature units: fahrenheit (with mph) or celsius (with km/h)")]


@mcp.tool()
async def get_current_weather(city: City, units: UnitsParam = "fahrenheit") -> str:
    """Get the current weather for any city in the world."""
    try:
        location = await weather.find_location(city)
        if location is None:
            return f"Couldn't find a city named {city}"
        current = await weather.current(location, units)
    except httpx.HTTPError as e:
        # Network down, timeout, or the API returned an error: tell the model instead of crashing
        return f"Weather service error: {e}"

    temp_unit, wind_unit = unit_labels(units)
    return (
        f"{location.name}: {current['temperature_2m']}{temp_unit}, "
        f"{describe_weather_code(current['weather_code'])}, "
        f"{current['relative_humidity_2m']}% humidity, "
        f"wind {current['wind_speed_10m']} {wind_unit}"
    )


@mcp.tool()
async def get_forecast(
    city: City,
    days: Annotated[int, Field(ge=1, le=16, description="Number of days to forecast, starting today (1-16)")] = 3,
    units: UnitsParam = "fahrenheit",
) -> str:
    """Get the daily weather forecast for any city in the world: highs, lows, conditions and chance of precipitation."""
    try:
        location = await weather.find_location(city)
        if location is None:
            return f"Couldn't find a city named {city}"
        forecast = await weather.forecast(location, days, units)
    except httpx.HTTPError as e:
        return f"Weather service error: {e}"

    temp_unit, _ = unit_labels(units)
    if not forecast:
        return f"No forecast data available for {location.name}"
    lines = [f"{len(forecast)}-day forecast for {location.name}:"]
    if len(forecast) < days:
        lines[0] += f" (only {len(forecast)} of the {days} requested days have data yet)"
    for day in forecast:
        label = date.fromisoformat(day["date"]).strftime("%a %b %d")  # e.g. "Thu Oct 16"
        line = (
            f"- {label}: high {day['high']}{temp_unit}, low {day['low']}{temp_unit}, "
            f"{describe_weather_code(day['weather_code'])}"
        )
        if day["precipitation_chance"] is not None:
            line += f", {day['precipitation_chance']}% chance of precipitation"
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Weather MCP server")
    parser.add_argument("--http", action="store_true", help="serve over Streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8000, help="port for --http (default 8000)")
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="streamable-http", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
