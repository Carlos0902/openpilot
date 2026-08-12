"""Bounded structured weather lookup used by the public web search tool."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


WeatherJSONGetter = Callable[[str, int], dict[str, Any]]


def weather_location(query: str) -> str:
    """Return one bounded explicit location from a weather question."""

    if not re.search(r"天气|weather", query, flags=re.IGNORECASE):
        return ""
    chinese = query
    for marker in (
        "怎么样", "如何", "请问", "告诉我", "一下", "今天", "今日", "明天",
        "后天", "现在", "当前", "实时", "天气", "的",
    ):
        chinese = chinese.replace(marker, " ")
    chinese_matches = re.findall(r"[\u4e00-\u9fff]+", chinese)
    if chinese_matches:
        location = chinese_matches[0]
        return location if 2 <= len(location) <= 40 else ""
    english_match = re.search(
        r"\bweather\s+(?:in|at|for)\s+([A-Za-z][A-Za-z .'-]{1,60})",
        query,
        flags=re.IGNORECASE,
    )
    return english_match.group(1).strip(" ?.! ")[:60] if english_match else ""


def structured_weather_result(
    *,
    query: str,
    location: str,
    timeout: int,
    json_get: WeatherJSONGetter | None = None,
) -> dict[str, Any]:
    """Fetch one bounded current/daily forecast as a search-artifact payload."""

    getter = json_get or _default_json_get
    encoded_location = quote(location, safe="")
    payload = getter(f"https://wttr.in/{encoded_location}?format=j1", timeout)
    current_items = payload.get("current_condition") if isinstance(payload, dict) else None
    forecast_items = payload.get("weather") if isinstance(payload, dict) else None
    if not isinstance(current_items, list) or not current_items or not isinstance(current_items[0], dict):
        raise ValueError("Weather provider returned no current condition")
    current = current_items[0]
    forecast = (
        forecast_items[0]
        if isinstance(forecast_items, list)
        and forecast_items
        and isinstance(forecast_items[0], dict)
        else {}
    )
    descriptions = current.get("weatherDesc")
    description = ""
    if isinstance(descriptions, list) and descriptions and isinstance(descriptions[0], dict):
        description = _bounded_text(descriptions[0].get("value"), maximum=160)
    hourly = forecast.get("hourly")
    rain_chances = [
        chance
        for item in (hourly[:24] if isinstance(hourly, list) else [])
        if isinstance(item, dict)
        for chance in [_bounded_number(item.get("chanceofrain"), minimum=0, maximum=100)]
        if chance is not None
    ]
    temperature = _bounded_number(current.get("temp_C"), minimum=-100, maximum=100)
    if temperature is None:
        raise ValueError("Weather provider returned an invalid current temperature")
    pieces = [f"{location}当前{temperature}°C"]
    feels_like = _bounded_number(current.get("FeelsLikeC"), minimum=-100, maximum=100)
    humidity = _bounded_number(current.get("humidity"), minimum=0, maximum=100)
    maximum = _bounded_number(forecast.get("maxtempC"), minimum=-100, maximum=100)
    minimum = _bounded_number(forecast.get("mintempC"), minimum=-100, maximum=100)
    if feels_like is not None:
        pieces.append(f"体感{feels_like}°C")
    if humidity is not None:
        pieces.append(f"湿度{humidity}%")
    if description:
        pieces.append(description)
    if maximum is not None and minimum is not None:
        pieces.append(f"今天最高{maximum}°C、最低{minimum}°C")
    if rain_chances:
        pieces.append(f"最高降雨概率{max(rain_chances)}%")
    summary = ("，".join(pieces) + "。")[:600]
    result_url = f"https://wttr.in/{encoded_location}"
    result = {
        "rank": 1,
        "title": f"{location}当前天气",
        "url": result_url,
        "snippet": summary,
        "source_domain": "wttr.in",
    }
    return {
        "query": query,
        "provider": "wttr_in",
        "effective_query": query,
        "search_attempts": [
            {
                "provider": "wttr_in",
                "query": query,
                "result_count": 1,
                "status": "success",
                "error": "",
            }
        ],
        "network_diagnostics": {},
        "results": [result],
        "count": 1,
        "pages": [],
        "llm_cleanup": False,
        "research_summary": summary,
        "key_points": [summary],
        "source_notes": [
            {"url": result_url, "note": "Structured current weather and daily forecast."}
        ],
        "follow_up_queries": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
    }


def _bounded_text(value: Any, *, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _bounded_number(value: Any, *, minimum: float, maximum: float) -> str | None:
    text = _bounded_text(value, maximum=16)
    if not re.fullmatch(r"-?\d{1,3}(?:\.\d)?", text):
        return None
    number = float(text)
    if number < minimum or number > maximum:
        return None
    return text


def _default_json_get(url: str, timeout: int) -> dict[str, Any]:
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=True) as client:
        response = client.get(url, headers={"User-Agent": "OpenPilotWeather/1.0"})
        response.raise_for_status()
        body = response.content
    if len(body) > 1_000_000:
        raise ValueError("Weather provider response exceeds the one-megabyte limit")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Weather provider returned a non-object payload")
    return payload


__all__ = ["structured_weather_result", "weather_location"]
