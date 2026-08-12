from metadata import ToolInputMetadata
from tools.web_searcher import web_searcher_executor


def test_weather_query_uses_structured_weather_source() -> None:
    def fake_weather_json_get(url: str, timeout: int):
        assert url.startswith("https://wttr.in/")
        assert timeout == 10
        return {
            "current_condition": [
                {
                    "temp_C": "26",
                    "FeelsLikeC": "28",
                    "humidity": "94",
                    "precipMM": "0.1",
                    "windspeedKmph": "24",
                    "weatherDesc": [{"value": "Patchy rain nearby"}],
                }
            ],
            "nearest_area": [
                {
                    "areaName": [{"value": "Changshu"}],
                    "region": [{"value": "Jiangsu"}],
                    "country": [{"value": "China"}],
                }
            ],
            "weather": [
                {
                    "maxtempC": "30",
                    "mintempC": "25",
                    "hourly": [{"chanceofrain": "70"}],
                }
            ],
        }

    result = web_searcher_executor(
        ToolInputMetadata.from_mapping(
            "web_searcher",
            {
                "query": "今天常熟的天气怎么样",
                "_weather_json_get": fake_weather_json_get,
            },
        )
    ).to_json_dict()["result"]

    assert result["provider"] == "wttr_in"
    assert result["count"] == 1
    assert "常熟" in result["research_summary"]
    assert "26°C" in result["research_summary"]
    assert result["results"][0]["source_domain"] == "wttr.in"


def test_weather_source_bounds_untrusted_description_and_hourly_values() -> None:
    def fake_weather_json_get(_url: str, _timeout: int):
        return {
            "current_condition": [
                {
                    "temp_C": "26",
                    "FeelsLikeC": "28",
                    "humidity": "94",
                    "weatherDesc": [{"value": "rain" * 1000}],
                }
            ],
            "weather": [
                {
                    "maxtempC": "30",
                    "mintempC": "25",
                    "hourly": [{"chanceofrain": "9" * 100}] * 1000,
                }
            ],
        }

    result = web_searcher_executor(
        ToolInputMetadata.from_mapping(
            "web_searcher",
            {
                "query": "今天常熟的天气怎么样",
                "_weather_json_get": fake_weather_json_get,
            },
        )
    ).to_json_dict()["result"]

    assert len(result["research_summary"]) <= 600
    assert "9" * 100 not in result["research_summary"]
