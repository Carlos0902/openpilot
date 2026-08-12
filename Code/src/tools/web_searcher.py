"""Web Searcher Tool - Search, fetch, and clean public web research results."""

from __future__ import annotations

import json
import os
import re
import time
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from metadata import (
    ContextRequestPurpose,
    ToolContractMetadata,
    ToolInputMetadata,
    ToolResultMetadata,
    metadata_tool_result,
)
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urldefrag, urljoin, urlparse

import httpx

from core.llm import LLMClient, LLMMessage
from memory.context_assembly import build_context_llm_request
from core.tool_contracts import (
    PermissionLevel,
    ToolCapability,
    ToolDefinition,
    ToolFailureMode,
)
from tools.weather_provider import (
    structured_weather_result as _structured_weather_result,
    weather_location as _weather_location,
)


WEB_SEARCHER_DEFINITION = ToolDefinition(
    name="web_searcher",
    display_name="Web Searcher",
    description="Search the public web, fetch result pages, and clean research content with an LLM",
    version="1.1.0",
    capabilities=[ToolCapability.WEB_SEARCH, ToolCapability.NETWORK, ToolCapability.LLM_CALL],
    permission_level=PermissionLevel.MEDIUM,
    contract_metadata=ToolContractMetadata(
        tool_name='web_searcher',
        input_metadata_type="ToolInputMetadata",
        output_metadata_type="ToolResultMetadata",
        required_input_fields=['query'],
        input_defaults={'max_results': 5, 'time_range': 'all', 'safe_search': 'moderate', 'max_search_attempts': 6, 'search_budget_seconds': 20, 'timeout': 10, 'max_pages': 3, 'max_page_chars': 4000, 'follow_redirects': True, 'max_redirect_depth': 1, 'max_redirect_pages': 2, 'max_redirect_candidates': 12, 'llm_cleanup': True, 'cleanup_instruction': 'Remove navigation, ads, duplicates, and unsupported claims. Organize facts by source and keep the answer concise.'},
    ),
    timeout_seconds=30,
    max_retries=1,
    failure_modes=[
        ToolFailureMode(
            error_type="invalid_input",
            description="Search parameters are invalid",
            recovery_strategy="Provide a non-empty query and supported search options",
        ),
        ToolFailureMode(
            error_type="network_error",
            description="Search provider request failed",
            recovery_strategy="Retry later or narrow the query",
        ),
        ToolFailureMode(
            error_type="parse_error",
            description="Search response could not be parsed",
            recovery_strategy="Retry later or use another provider",
        ),
        ToolFailureMode(
            error_type="llm_error",
            description="LLM cleanup failed",
            recovery_strategy="Disable llm_cleanup or check LLM provider configuration",
        ),
    ],
    tags=["web", "search", "research", "internet", "network", "llm"],
    audit_required=True,
)


TIME_RANGE_PARAMS = {
    "all": None,
    "day": "d",
    "week": "w",
    "month": "m",
    "year": "y",
}

SAFE_SEARCH_PARAMS = {
    "off": "-2",
    "moderate": "-1",
    "strict": "1",
}


class SearchResultHTMLParser(HTMLParser):
    """Extract result title/link/snippet triples from simple search-result HTML."""

    def __init__(self, provider: str) -> None:
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._bing_result_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name: value or "" for name, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if self.provider == "bing_html" and "b_algo" in classes:
            self._finish_current()
            self._bing_result_depth = 1
            return
        if self._bing_result_depth:
            self._bing_result_depth += 1

        if tag == "a" and self._is_result_link(classes, attrs_dict):
            self._finish_current()
            self._current = {
                "title": "",
                "url": _normalize_result_url(attrs_dict.get("href", ""), self.provider),
                "snippet": "",
            }
            self._capture_title = True
            self._title_parts = []
            return

        if self._current is not None and self._is_snippet(tag, classes):
            self._capture_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title and self._current is not None:
            self._current["title"] = _clean_text(" ".join(self._title_parts))
            self._capture_title = False

        if self._capture_snippet and self._current is not None and tag in {"a", "div", "span"}:
            self._current["snippet"] = _clean_text(" ".join(self._snippet_parts))
            self._capture_snippet = False

        if self._capture_snippet and self.provider == "bing_html" and tag == "p" and self._current is not None:
            self._current["snippet"] = _clean_text(" ".join(self._snippet_parts))
            self._capture_snippet = False

        if self._bing_result_depth:
            self._bing_result_depth -= 1
            if self._bing_result_depth == 0:
                self._finish_current()

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_parts.append(data)
        if self._capture_snippet:
            self._snippet_parts.append(data)

    def close(self) -> None:
        super().close()
        self._finish_current()

    def _finish_current(self) -> None:
        if not self._current:
            return
        url = self._current.get("url", "")
        if self._current.get("title") and url and not _is_search_engine_internal_url(url):
            self.results.append(self._current)
        self._current = None

    def _is_result_link(self, classes: set[str], attrs: dict[str, str]) -> bool:
        href = attrs.get("href", "")
        if self.provider == "google_html":
            url = _normalize_result_url(href, self.provider)
            return bool(url and not _is_search_engine_internal_url(url))
        if self.provider == "bing_html":
            url = _normalize_result_url(href, self.provider)
            return self._bing_result_depth > 0 and bool(url and not _is_search_engine_internal_url(url))
        if self.provider == "duckduckgo_html":
            url = _normalize_result_url(href, self.provider)
            return "result__a" in classes and bool(url and not _is_search_engine_internal_url(url))
        return False

    def _is_snippet(self, tag: str, classes: set[str]) -> bool:
        if self.provider == "bing_html":
            return self._bing_result_depth > 0 and tag == "p"
        if self.provider == "duckduckgo_html":
            return "result__snippet" in classes
        return False


class ReadableHTMLTextParser(HTMLParser):
    """Extract visible-ish text from simple HTML pages."""

    BLOCK_TAGS = {"article", "main", "section", "p", "div", "li", "h1", "h2", "h3", "h4", "blockquote"}
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "nav", "footer", "header", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._title_parts: list[str] = []
        self._capture_title = False
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._capture_title = True
            self._title_parts = []
            return
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self.title = _clean_text(" ".join(self._title_parts))
            self._capture_title = False
            return
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._capture_title:
            self._title_parts.append(data)
            return
        text = _clean_text(data)
        if text:
            self._parts.append(text)

    def readable_text(self) -> str:
        lines = []
        for line in " ".join(self._parts).splitlines():
            cleaned = _clean_text(line)
            if len(cleaned) >= 20:
                lines.append(cleaned)
        return "\n".join(_dedupe_preserve_order(lines))


class LinkHTMLParser(HTMLParser):
    """Extract visible page links with nearby anchor text."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._skip_depth = 0
        self._current_href = ""
        self._current_parts: list[str] = []
        self._current_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if self._current_href:
            self._current_depth += 1
            return
        if tag != "a":
            return
        attrs_dict = {name: value or "" for name, value in attrs}
        href = _normalize_page_link(self.base_url, attrs_dict.get("href", ""))
        if not href:
            return
        self._current_href = href
        self._current_parts = []
        self._current_depth = 0

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if not self._current_href:
            return
        if tag == "a" and self._current_depth == 0:
            text = _clean_text(" ".join(self._current_parts))
            self.links.append(
                {
                    "url": self._current_href,
                    "anchor_text": text,
                    "source_domain": _domain_from_url(self._current_href),
                }
            )
            self._current_href = ""
            self._current_parts = []
            return
        if self._current_depth:
            self._current_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._current_href:
            return
        self._current_parts.append(data)


class SearchFallbackLinkParser(HTMLParser):
    """Extract external result-like links when provider-specific parsing fails."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}

    def __init__(self, provider: str) -> None:
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.results: list[dict[str, str]] = []
        self._skip_depth = 0
        self._current_url = ""
        self._current_parts: list[str] = []
        self._current_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if self._current_url:
            self._current_depth += 1
            return
        if tag != "a":
            return
        attrs_dict = {name: value or "" for name, value in attrs}
        url = _normalize_result_url(attrs_dict.get("href", ""), self.provider)
        if not url or _is_low_value_search_result_url(url):
            return
        self._current_url = url
        self._current_parts = []
        self._current_depth = 0

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if not self._current_url:
            return
        if tag == "a" and self._current_depth == 0:
            title = _clean_text(" ".join(self._current_parts)) or _domain_from_url(self._current_url)
            self.results.append({"title": title, "url": self._current_url, "snippet": ""})
            self._current_url = ""
            self._current_parts = []
            return
        if self._current_depth:
            self._current_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._current_url:
            return
        self._current_parts.append(data)


class SearchProviderBlockedError(RuntimeError):
    """Raised when a search provider explicitly blocks the request."""


@metadata_tool_result('web_searcher')
def web_searcher_executor(input_metadata: ToolInputMetadata) -> ToolResultMetadata:
    params = input_metadata.to_params()
    """Search the web, fetch result pages, and return cleaned research data."""
    query = str(params.get("query", "")).strip()
    if not query:
        raise ValueError("Search query cannot be empty")

    max_results = _clamp_int(params.get("max_results", 5), minimum=1, maximum=10)
    max_pages = _clamp_int(params.get("max_pages", 3), minimum=0, maximum=5)
    max_page_chars = _clamp_int(params.get("max_page_chars", 4000), minimum=500, maximum=12000)
    max_search_attempts = _clamp_int(params.get("max_search_attempts", 6), minimum=1, maximum=10)
    search_budget_seconds = _clamp_int(params.get("search_budget_seconds", 20), minimum=3, maximum=30)
    max_redirect_depth = _clamp_int(params.get("max_redirect_depth", 1), minimum=0, maximum=2)
    max_redirect_pages = _clamp_int(params.get("max_redirect_pages", 2), minimum=0, maximum=5)
    max_redirect_candidates = _clamp_int(params.get("max_redirect_candidates", 12), minimum=5, maximum=30)
    time_range = str(params.get("time_range", "all") or "all").lower()
    safe_search = str(params.get("safe_search", "moderate") or "moderate").lower()
    timeout = _clamp_int(params.get("timeout", 10), minimum=1, maximum=30)
    follow_redirects = params.get("follow_redirects", True)
    if not isinstance(follow_redirects, bool):
        raise ValueError("follow_redirects must be a boolean")
    llm_cleanup_requested = params.get("llm_cleanup", True)
    if not isinstance(llm_cleanup_requested, bool):
        raise ValueError("llm_cleanup must be a boolean")
    cleanup_failure_policy = str(params.get("_cleanup_failure_policy", "raise") or "raise")
    if cleanup_failure_policy not in {"raise", "return_raw"}:
        raise ValueError("Unsupported _cleanup_failure_policy")
    cleanup_instruction = str(
        params.get(
            "cleanup_instruction",
            "Remove navigation, ads, duplicates, and unsupported claims. Organize facts by source and keep the answer concise.",
        )
        or ""
    )

    if time_range not in TIME_RANGE_PARAMS:
        raise ValueError(f"Unsupported time_range: {time_range}")
    if safe_search not in SAFE_SEARCH_PARAMS:
        raise ValueError(f"Unsupported safe_search: {safe_search}")

    weather_location = _weather_location(query)
    if weather_location:
        _load_dotenv_for_proxy()
        return _structured_weather_result(
            query=query,
            location=weather_location,
            timeout=timeout,
            json_get=params.get("_weather_json_get"),
        )

    http_get = params.get("_http_get") or _default_http_get
    network_diagnostics = _network_diagnostics()

    query_variants, variant_warnings = _build_search_query_variants(
        query=query,
        llm_client=params.get("_llm_client"),
        max_variants=max_search_attempts,
    )
    provider = ""
    effective_query = ""
    parsed_results: list[dict[str, str]] = []
    warnings = []
    warnings.extend(variant_warnings)
    search_attempts = []
    search_started_at = time.monotonic()
    attempt_count = 0
    search_budget_exhausted = False
    for query_index, search_query in enumerate(query_variants):
        if attempt_count >= max_search_attempts:
            break
        if time.monotonic() - search_started_at >= search_budget_seconds:
            search_budget_exhausted = _append_search_budget_warning(
                warnings,
                search_budget_seconds,
                already_appended=search_budget_exhausted,
            )
            break
        for candidate_provider in ("google_html", "bing_html", "duckduckgo_html"):
            if attempt_count >= max_search_attempts:
                break
            remaining_variants = len(query_variants) - query_index - 1
            remaining_attempts = max_search_attempts - attempt_count
            if candidate_provider == "duckduckgo_html" and remaining_variants and remaining_attempts <= remaining_variants:
                break
            elapsed = time.monotonic() - search_started_at
            if elapsed >= search_budget_seconds:
                search_budget_exhausted = _append_search_budget_warning(
                    warnings,
                    search_budget_seconds,
                    already_appended=search_budget_exhausted,
                )
                break
            attempt_count += 1
            if candidate_provider == "google_html":
                search_url = _build_google_search_url(search_query, safe_search, time_range)
            elif candidate_provider == "bing_html":
                search_url = _build_bing_search_url(search_query, safe_search, time_range)
            else:
                search_url = _build_duckduckgo_search_url(search_query, safe_search, time_range)
            attempt = {
                "provider": candidate_provider,
                "query": search_query,
                "result_count": 0,
                "status": "error",
                "error": "",
            }
            search_attempts.append(attempt)
            remaining_timeout = max(1, min(timeout, int(search_budget_seconds - elapsed) or 1))
            try:
                html = http_get(search_url, remaining_timeout)
                blocked_reason = _blocked_search_response_reason(html, candidate_provider)
                if blocked_reason:
                    attempt["status"] = "blocked"
                    attempt["error"] = blocked_reason
                    continue
                candidate_results = _parse_search_results(html, candidate_provider)
                attempt["result_count"] = len(candidate_results)
                if candidate_results:
                    attempt["status"] = "success"
                    parsed_results = candidate_results
                    provider = candidate_provider
                    effective_query = search_query
                    break
                attempt["status"] = "empty"
                attempt["error"] = "No search results parsed from provider response"
            except SearchProviderBlockedError as exc:
                attempt["status"] = "blocked"
                attempt["error"] = str(exc)
            except TimeoutError as exc:
                attempt["status"] = "timeout"
                attempt["error"] = f"timed out ({exc})"
            except (HTTPError, URLError, OSError) as exc:
                attempt["status"] = "network_error"
                attempt["error"] = _network_error_message(exc, network_diagnostics)
            except Exception as exc:
                blocked_status = _blocked_http_status(exc)
                if blocked_status:
                    attempt["status"] = "blocked"
                    attempt["error"] = f"Search provider returned HTTP {blocked_status}"
                    continue
                attempt["status"] = "network_error" if _is_network_exception(exc) else "parse_error"
                attempt["error"] = (
                    _network_error_message(exc, network_diagnostics)
                    if attempt["status"] == "network_error"
                    else str(exc)
                )
        if parsed_results:
            break

    if not parsed_results:
        provider = search_attempts[-1]["provider"] if search_attempts else ""
        warnings.append("No search results found after trying Google/Bing/DuckDuckGo query variants.")
        if search_attempts and all(attempt.get("status") in {"network_error", "blocked", "timeout"} for attempt in search_attempts):
            warnings.append(_network_failure_warning(network_diagnostics))
        for attempt in search_attempts:
            if attempt.get("status") != "success":
                warnings.append(
                    "Search attempt failed: "
                    f"{attempt['provider']} query={attempt['query']!r} "
                    f"status={attempt['status']} error={attempt['error']}"
                )

    results = [
        {
            "rank": index + 1,
            "title": item["title"],
            "url": item["url"],
            "snippet": item.get("snippet", ""),
            "source_domain": _domain_from_url(item["url"]),
        }
        for index, item in enumerate(parsed_results[:max_results])
    ]

    pages, page_links = _fetch_result_pages(
        results=results[:max_pages],
        http_get=http_get,
        timeout=timeout,
        max_page_chars=max_page_chars,
        warnings=warnings,
    )
    if follow_redirects and max_redirect_depth > 0 and max_redirect_pages > 0 and pages:
        _follow_selected_redirect_links(
            query=effective_query or query,
            results=results,
            pages=pages,
            page_links=page_links,
            http_get=http_get,
            timeout=timeout,
            max_page_chars=max_page_chars,
            max_redirect_depth=max_redirect_depth,
            max_redirect_pages=max_redirect_pages,
            max_redirect_candidates=max_redirect_candidates,
            warnings=warnings,
            llm_client=params.get("_llm_client"),
        )
    cleanup_payload = _empty_cleanup_payload()
    llm_cleanup_executed = False
    llm_cleanup_error = None
    if llm_cleanup_requested and pages:
        try:
            cleanup_payload = _clean_with_llm(
                query=effective_query or query,
                results=results,
                pages=pages,
                cleanup_instruction=cleanup_instruction,
                llm_client=params.get("_llm_client"),
            )
            llm_cleanup_executed = True
        except RuntimeError as exc:
            if cleanup_failure_policy != "return_raw":
                raise
            llm_cleanup_error = str(exc)
            warnings.append(
                "LLM cleanup failed, so Agent Generator continued with raw web search results. "
                f"Reason: {llm_cleanup_error}"
            )

    output = {
        "query": query,
        "provider": provider,
        "effective_query": effective_query,
        "search_attempts": search_attempts,
        "network_diagnostics": network_diagnostics,
        "results": results,
        "count": len(results),
        "pages": pages,
        "llm_cleanup": llm_cleanup_executed,
        **cleanup_payload,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings,
    }
    if llm_cleanup_error:
        output["llm_cleanup_error"] = llm_cleanup_error
    return output


def _build_search_query_variants(
    *,
    query: str,
    llm_client: Any | None,
    max_variants: int,
) -> tuple[list[str], list[str]]:
    warnings = []
    local_variants = _local_search_query_variants(query)
    llm_variants = []
    if llm_client is not None:
        try:
            llm_variants = _llm_search_query_variants(query=query, llm_client=llm_client)
        except RuntimeError as exc:
            warnings.append(f"LLM keyword generation failed; using local query variants. Reason: {exc}")

    if _has_explicit_query_subject(query):
        variants = [*local_variants, *llm_variants]
    else:
        variants = [*llm_variants, *local_variants]
    variants.append(query)
    cleaned = []
    for variant in variants:
        normalized = _clean_query_variant(variant)
        if normalized:
            cleaned.append(normalized)
    deduped = _dedupe_preserve_order(cleaned)
    return deduped[:max_variants] or [query], warnings


def _local_search_query_variants(query: str) -> list[str]:
    slots = _extract_query_slots(query)
    base_query = _strip_query_slots(query)
    subject = slots.get("subject") or slots.get("topic") or slots.get("主题") or ""
    subject = _clean_query_variant(subject)
    depth = slots.get("depth") or slots.get("scope") or ""
    time_focus = slots.get("time_focus") or slots.get("time") or ""
    language = slots.get("language") or ""

    modifiers = []
    if _matches_any(depth, {"overview", "整体概述", "概述", "intro", "入门"}):
        modifiers.append("概述" if _contains_cjk(query) else "overview")
    if _matches_any(time_focus, {"latest", "最新", "recent", "current"}):
        modifiers.append("最新" if _contains_cjk(query) else "latest")
    if _matches_any(language, {"中文", "chinese", "zh"}):
        modifiers.append("中文")

    variants = []
    if subject:
        variants.append(" ".join([subject, *modifiers]))
        if modifiers:
            variants.append(subject)
        english_modifiers = []
        if _matches_any(depth, {"overview", "整体概述", "概述", "intro", "入门"}):
            english_modifiers.append("overview")
        if _matches_any(time_focus, {"latest", "最新", "recent", "current"}):
            english_modifiers.append("latest")
        if english_modifiers:
            variants.append(" ".join([subject, *english_modifiers]))
    if base_query and base_query != subject:
        variants.append(base_query)
    return variants


def _has_explicit_query_subject(query: str) -> bool:
    slots = _extract_query_slots(query)
    return any(slots.get(key) for key in ("subject", "topic", "主题"))


def _llm_search_query_variants(*, query: str, llm_client: Any) -> list[str]:
    prompt = (
        "Rewrite the user's natural-language task into 3 to 5 concise web search queries. "
        "Remove request/action wording and keep only searchable subject matter plus important "
        "scope, language, recency, source, or output constraints. Prefer short keyword-style "
        "queries over long sentences. Return plain text only, one query per line.\n\n"
        f"Request:\n{query}"
    )
    try:
        response = llm_client.complete(
            build_context_llm_request(
                llm_client,
                purpose=ContextRequestPurpose.WEB_QUERY_GENERATION,
                messages=[
                    LLMMessage(
                        role="system",
                        content="You turn user research requests into concise Google/Bing search keywords.",
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                response_format="text",
                temperature=0.1,
                max_tokens=220,
                trace_info={"tool": "web_searcher", "task": "keyword_generation"},
            )
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    return [_clean_query_variant(line) for line in response.content.splitlines() if _clean_query_variant(line)]


def _extract_query_slots(query: str) -> dict[str, str]:
    slots = {}
    for match in re.finditer(r"([\w\u4e00-\u9fff]+):\s*(.*?)(?=\s+[\w\u4e00-\u9fff]+:\s*|$)", query):
        key = match.group(1).strip().lower()
        value = _clean_text(match.group(2))
        if key and value:
            slots[key] = value
    return slots


def _strip_query_slots(query: str) -> str:
    stripped = re.sub(r"\s*[\w\u4e00-\u9fff]+:\s*.*?(?=\s+[\w\u4e00-\u9fff]+:\s*|$)", " ", query)
    return _clean_query_variant(stripped)


def _clean_query_variant(value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = re.sub(r"^[-*+\d.)\s]+", "", cleaned).strip("'\"` ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:160].strip()


def _append_search_budget_warning(
    warnings: list[str],
    search_budget_seconds: int,
    *,
    already_appended: bool,
) -> bool:
    if not already_appended:
        warnings.append(f"Search budget exhausted after {search_budget_seconds}s.")
    return True


def _matches_any(value: str, expected: set[str]) -> bool:
    normalized = value.lower()
    return any(item.lower() in normalized for item in expected)


def _build_google_search_url(query: str, safe_search: str, time_range: str) -> str:
    query_params = {
        "q": query,
        "hl": "zh-CN" if _contains_cjk(query) else "en",
        "safe": "off" if safe_search == "off" else "active",
    }
    time_param = TIME_RANGE_PARAMS[time_range]
    if time_param:
        query_params["tbs"] = f"qdr:{time_param}"
    return f"https://www.google.com/search?{urlencode(query_params, quote_via=quote_plus)}"


def _build_bing_search_url(query: str, safe_search: str, time_range: str) -> str:
    query_params = {
        "q": query,
        "safeSearch": {
            "off": "Off",
            "moderate": "Moderate",
            "strict": "Strict",
        }[safe_search],
    }
    freshness = {
        "day": "Day",
        "week": "Week",
        "month": "Month",
        "year": "Year",
    }.get(time_range)
    if freshness:
        query_params["freshness"] = freshness
    return f"https://www.bing.com/search?{urlencode(query_params, quote_via=quote_plus)}"


def _build_duckduckgo_search_url(query: str, safe_search: str, time_range: str) -> str:
    query_params = {
        "q": query,
        "kp": SAFE_SEARCH_PARAMS[safe_search],
    }
    freshness = {
        "day": "d",
        "week": "w",
        "month": "m",
        "year": "y",
    }.get(time_range)
    if freshness:
        query_params["df"] = freshness
    return f"https://html.duckduckgo.com/html/?{urlencode(query_params, quote_via=quote_plus)}"


def _default_http_get(url: str, timeout: int) -> str:
    _load_dotenv_for_proxy()
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; OpenPilotWebSearcher/1.0; "
                "+https://example.invalid/openpilot)"
            )
        },
    ) as client:
        response = client.get(url)
        status_code = int(getattr(response, "status_code", 200))
        if status_code in {403, 429, 503}:
            raise SearchProviderBlockedError(f"Search provider returned HTTP {status_code}")
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.text


def _load_dotenv_for_proxy() -> bool:
    try:
        from dotenv import load_dotenv

        candidates = [Path.cwd(), *Path.cwd().parents]
        for directory in candidates:
            env_path = directory / ".env"
            if env_path.is_file():
                return bool(load_dotenv(env_path, override=False))
        return bool(load_dotenv(override=False))
    except Exception:
        return False


def _parse_search_results(html: str, provider: str) -> list[dict[str, str]]:
    parser = SearchResultHTMLParser(provider)
    parser.feed(html)
    parser.close()
    if parser.results:
        return parser.results
    if provider not in {"google_html", "bing_html", "duckduckgo_html"}:
        return []
    fallback_parser = SearchFallbackLinkParser(provider)
    fallback_parser.feed(html)
    fallback_parser.close()
    return _dedupe_search_results(fallback_parser.results)


def _fetch_result_pages(
    *,
    results: list[dict[str, Any]],
    http_get: Any,
    timeout: int,
    max_page_chars: int,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    pages = []
    page_links = {}
    for result in results:
        page = {
            "url": result["url"],
            "title": result.get("title", ""),
            "source_domain": result.get("source_domain", ""),
            "content_excerpt": "",
            "content_chars": 0,
            "fetch_success": False,
            "error": None,
            "fetch_depth": int(result.get("fetch_depth", 0) or 0),
            "parent_url": result.get("parent_url"),
            "selected_by_llm": bool(result.get("selected_by_llm", False)),
            "selection_reason": str(result.get("selection_reason", "") or ""),
        }
        try:
            html = http_get(result["url"], timeout)
            title, text, links = _extract_readable_page_text_and_links(html, result["url"])
            excerpt = text[:max_page_chars]
            page.update(
                {
                    "title": title or page["title"],
                    "content_excerpt": excerpt,
                    "content_chars": len(excerpt),
                    "fetch_success": True,
                }
            )
            page_links[page["url"]] = links
        except Exception as exc:
            page["error"] = str(exc)
            warnings.append(f"Failed to fetch {result['url']}: {exc}")
        pages.append(page)
    return pages, page_links


def _extract_readable_page_text(html: str) -> tuple[str, str]:
    title, text, _links = _extract_readable_page_text_and_links(html, "")
    return title, text


def _extract_readable_page_text_and_links(html: str, base_url: str) -> tuple[str, str, list[dict[str, str]]]:
    parser = ReadableHTMLTextParser()
    parser.feed(html)
    parser.close()
    link_parser = LinkHTMLParser(base_url)
    link_parser.feed(html)
    link_parser.close()
    return parser.title, parser.readable_text(), _dedupe_links(link_parser.links)


def _follow_selected_redirect_links(
    *,
    query: str,
    results: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    page_links: dict[str, list[dict[str, str]]],
    http_get: Any,
    timeout: int,
    max_page_chars: int,
    max_redirect_depth: int,
    max_redirect_pages: int,
    max_redirect_candidates: int,
    warnings: list[str],
    llm_client: Any | None,
) -> None:
    visited_urls = {page["url"] for page in pages}
    frontier = list(pages)
    for depth in range(1, max_redirect_depth + 1):
        candidates = _collect_redirect_candidates(
            frontier=frontier,
            page_links=page_links,
            visited_urls=visited_urls,
            max_redirect_candidates=max_redirect_candidates,
        )
        if not candidates:
            return
        try:
            selected = _select_redirect_links_with_llm(
                query=query,
                results=results,
                pages=pages,
                candidates=candidates,
                max_redirect_pages=max_redirect_pages,
                llm_client=llm_client,
            )
        except RuntimeError as exc:
            warnings.append(f"LLM redirect selection failed; stopped following page links. Reason: {exc}")
            return
        if not selected:
            return

        selection_by_url = {item["url"]: item for item in selected}
        redirect_results = []
        for item in selected:
            if item["url"] in visited_urls:
                continue
            visited_urls.add(item["url"])
            redirect_results.append(
                {
                    "url": item["url"],
                    "title": item.get("anchor_text", ""),
                    "source_domain": item.get("source_domain", _domain_from_url(item["url"])),
                    "fetch_depth": depth,
                    "parent_url": item.get("parent_url"),
                    "selected_by_llm": True,
                    "selection_reason": item.get("selection_reason", ""),
                }
            )
        if not redirect_results:
            return

        fetched_pages, fetched_links = _fetch_result_pages(
            results=redirect_results,
            http_get=http_get,
            timeout=timeout,
            max_page_chars=max_page_chars,
            warnings=warnings,
        )
        for page in fetched_pages:
            selected_item = selection_by_url.get(page["url"])
            if selected_item:
                page["selection_reason"] = selected_item.get("selection_reason", "")
        pages.extend(fetched_pages)
        page_links.update(fetched_links)
        frontier = fetched_pages


def _collect_redirect_candidates(
    *,
    frontier: list[dict[str, Any]],
    page_links: dict[str, list[dict[str, str]]],
    visited_urls: set[str],
    max_redirect_candidates: int,
) -> list[dict[str, str]]:
    candidates = []
    seen = set(visited_urls)
    for page in frontier:
        if not page.get("fetch_success"):
            continue
        parent_url = str(page.get("url", "") or "")
        parent_title = str(page.get("title", "") or "")
        for link in page_links.get(parent_url, []):
            url = link.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(
                {
                    "id": f"L{len(candidates) + 1}",
                    "url": url,
                    "anchor_text": link.get("anchor_text", ""),
                    "source_domain": link.get("source_domain", _domain_from_url(url)),
                    "parent_url": parent_url,
                    "parent_title": parent_title,
                }
            )
            if len(candidates) >= max_redirect_candidates:
                return candidates
    return candidates


def _select_redirect_links_with_llm(
    *,
    query: str,
    results: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    candidates: list[dict[str, str]],
    max_redirect_pages: int,
    llm_client: Any | None,
) -> list[dict[str, str]]:
    if llm_client is None:
        from core.config import LLMSettings

        llm_client = LLMClient(LLMSettings())

    prompt = _build_redirect_selection_prompt(
        query=query,
        results=results,
        pages=pages,
        candidates=candidates,
        max_redirect_pages=max_redirect_pages,
    )
    try:
        response = llm_client.complete(
            build_context_llm_request(
                llm_client,
                purpose=ContextRequestPurpose.WEB_LINK_SELECTION,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You choose which page links are worth fetching next for web research. "
                            "Use only the candidate IDs supplied by the tool."
                        ),
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                response_format="text",
                temperature=0.0,
                max_tokens=300,
                trace_info={"tool": "web_searcher", "task": "link_selection"},
            )
        )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    return _parse_redirect_selection(response.content, candidates, max_redirect_pages)


def _build_redirect_selection_prompt(
    *,
    query: str,
    results: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    candidates: list[dict[str, str]],
    max_redirect_pages: int,
) -> str:
    context = {
        "query": query,
        "search_results": results[:5],
        "fetched_pages": [
            {
                "url": page["url"],
                "title": page["title"],
                "source_domain": page["source_domain"],
                "excerpt": str(page.get("content_excerpt", ""))[:700],
            }
            for page in pages[-5:]
            if page.get("fetch_success")
        ],
        "candidate_links": candidates,
    }
    return (
        "Select up to "
        f"{max_redirect_pages} candidate links that are most likely to add useful, source-grounded information. "
        "Ignore ads, login pages, sharing links, generic navigation, duplicate sources, and unrelated pages.\n"
        "Return plain text only, one selected link per line in this exact form:\n"
        "<candidate ID> | <short reason>\n"
        "If no link is worth fetching, return exactly: NONE\n\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
    )


def _parse_redirect_selection(
    content: str,
    candidates: list[dict[str, str]],
    max_redirect_pages: int,
) -> list[dict[str, str]]:
    if not content or content.strip().upper() == "NONE":
        return []
    by_id = {candidate["id"].upper(): candidate for candidate in candidates}
    selected = []
    seen_urls = set()
    for line in content.splitlines():
        match = re.match(r"^\s*(L\d+)\s*(?:\|\s*(.*))?$", line.strip(), flags=re.IGNORECASE)
        if not match:
            continue
        candidate = by_id.get(match.group(1).upper())
        if not candidate or candidate["url"] in seen_urls:
            continue
        item = dict(candidate)
        item["selection_reason"] = _clean_text(match.group(2) or "")
        selected.append(item)
        seen_urls.add(candidate["url"])
        if len(selected) >= max_redirect_pages:
            break
    return selected


def _clean_with_llm(
    *,
    query: str,
    results: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    cleanup_instruction: str,
    llm_client: Any | None,
) -> dict[str, Any]:
    if llm_client is None:
        from core.config import LLMSettings

        llm_client = LLMClient(LLMSettings())

    prompt = _build_cleanup_prompt(
        query=query,
        results=results,
        pages=pages,
        cleanup_instruction=cleanup_instruction,
    )
    try:
        response = llm_client.complete(
            build_context_llm_request(
                llm_client,
                purpose=ContextRequestPurpose.WEB_CLEANUP,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You clean and organize web research. Use only the supplied "
                            "search results and fetched page excerpts. Do not invent facts."
                        ),
                    ),
                    LLMMessage(role="user", content=prompt),
                ],
                response_format="text",
                temperature=0.1,
                max_tokens=900,
                trace_info={"tool": "web_searcher", "task": "cleanup"},
            )
        )
    except Exception as exc:
        raise RuntimeError(f"LLM cleanup failed: {exc}") from exc

    markdown = response.content.strip()
    return _cleanup_payload_from_markdown(markdown)


def _build_cleanup_prompt(
    *,
    query: str,
    results: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    cleanup_instruction: str,
) -> str:
    payload = {
        "query": query,
        "cleanup_instruction": cleanup_instruction,
        "search_results": results,
        "fetched_pages": [
            {
                "url": page["url"],
                "title": page["title"],
                "source_domain": page["source_domain"],
                "content_excerpt": page["content_excerpt"],
                "fetch_success": page["fetch_success"],
                "error": page["error"],
            }
            for page in pages
        ],
    }
    return (
        "Clean noisy webpage text and organize the research. Return ONLY compact Markdown. "
        "Do not wrap it in JSON or a code fence. Use only supplied sources. Prefer these short sections "
        "when useful: Summary, Key Points, Source Notes, Follow-up Queries.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _cleanup_payload_from_markdown(markdown: str) -> dict[str, Any]:
    summary = markdown.strip()
    return {
        "research_summary": summary,
        "key_points": _markdown_bullets_for_section(
            summary,
            {"key points", "key findings", "findings", "要点", "关键点", "重点"},
        ),
        "source_notes": _markdown_source_notes(summary),
        "follow_up_queries": _markdown_bullets_for_section(
            summary,
            {"follow-up queries", "follow up queries", "followups", "follow-up", "后续查询", "后续问题"},
        ),
    }


def _markdown_bullets_for_section(markdown: str, headings: set[str]) -> list[str]:
    lines = markdown.splitlines()
    in_section = False
    bullets = []
    for line in lines:
        heading = _markdown_heading_text(line)
        if heading:
            if in_section:
                break
            in_section = heading in headings
            continue
        if not in_section:
            continue
        bullet = _markdown_bullet_text(line)
        if bullet:
            bullets.append(bullet)
    return bullets


def _markdown_source_notes(markdown: str) -> list[dict[str, str]]:
    bullets = _markdown_bullets_for_section(
        markdown,
        {"source notes", "sources", "source-specific notes", "来源说明", "来源"},
    )
    notes = []
    for bullet in bullets:
        match = re.search(r"(https?://[^\s)]+)\s*[:：\-–—]?\s*(.*)", bullet)
        if not match:
            continue
        notes.append({"url": match.group(1).rstrip(".,;:："), "note": match.group(2).strip()})
    return notes


def _markdown_heading_text(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if _markdown_bullet_text(stripped):
        return ""
    if stripped.startswith("#"):
        stripped = stripped.lstrip("#").strip()
    elif stripped.endswith(":"):
        stripped = stripped[:-1].strip()
    else:
        return ""
    if not stripped or len(stripped) > 80:
        return ""
    return stripped.lower()


def _markdown_bullet_text(line: str) -> str:
    stripped = line.strip()
    match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", stripped)
    if not match:
        return ""
    return match.group(1).strip()


def _empty_cleanup_payload() -> dict[str, Any]:
    return {
        "research_summary": "",
        "key_points": [],
        "source_notes": [],
        "follow_up_queries": [],
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _source_notes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    notes = []
    for item in value:
        if isinstance(item, dict):
            notes.append(
                {
                    "url": str(item.get("url", "") or ""),
                    "note": str(item.get("note", "") or ""),
                }
            )
        elif item is not None:
            notes.append({"url": "", "note": str(item)})
    return notes


def _normalize_result_url(url: str, provider: str = "") -> str:
    url = unescape(url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = f"https:{url}"
    if url.startswith("/"):
        base_url = {
            "bing_html": "https://www.bing.com",
            "duckduckgo_html": "https://html.duckduckgo.com",
        }.get(provider, "https://www.google.com")
        url = urljoin(base_url, url)

    parsed = urlparse(url)
    if "google." in parsed.netloc and parsed.path == "/url":
        query_values = parse_qs(parsed.query)
        target = (query_values.get("q") or query_values.get("url") or [""])[0]
        if target:
            return _normalize_result_url(unquote(target), provider)
    if "google." in parsed.netloc and parsed.path.startswith("/search"):
        return ""
    if parsed.netloc.lower().removeprefix("www.").endswith("bing.com") and parsed.path.startswith("/ck/"):
        target = _decode_bing_redirect_target(parsed.query)
        if target:
            return _normalize_result_url(target, provider)
        return ""
    if parsed.netloc.lower().removeprefix("www.") in {"duckduckgo.com", "html.duckduckgo.com"}:
        target = (parse_qs(parsed.query).get("uddg") or [""])[0]
        if target:
            return _normalize_result_url(unquote(target), provider)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if _is_search_engine_internal_url(url):
        return ""
    return urldefrag(url).url


def _decode_bing_redirect_target(query: str) -> str:
    values = parse_qs(query).get("u") or []
    if not values:
        return ""
    raw = unquote(values[0]).strip()
    if raw.startswith(("http://", "https://")):
        return raw
    candidates = [raw]
    if raw.startswith(("a1", "a2")):
        candidates.append(raw[2:])
    for candidate in candidates:
        padded = candidate + ("=" * (-len(candidate) % 4))
        try:
            decoded = urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        if decoded.startswith(("http://", "https://")):
            return decoded
    return ""


def _is_search_engine_internal_url(url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    if not domain:
        return True
    if (
        domain == "google.com"
        or domain.startswith("google.")
        or domain.startswith(("accounts.google.", "policies.google.", "support.google."))
        or domain.endswith("googleusercontent.com")
    ):
        return True
    if domain.endswith("bing.com") or domain.endswith("microsoft.com"):
        return parsed.path.startswith(("/search", "/ck/", "/aclick", "/images", "/videos", "/maps"))
    if domain in {"duckduckgo.com", "html.duckduckgo.com"}:
        return True
    return False


def _normalize_page_link(base_url: str, href: str) -> str:
    href = unescape(href or "").strip()
    if not href or href.startswith("#"):
        return ""
    parsed_href = urlparse(href)
    if parsed_href.scheme and parsed_href.scheme.lower() not in {"http", "https"}:
        return ""
    if not parsed_href.scheme and href.lower().startswith(("mailto:", "javascript:", "tel:", "data:")):
        return ""
    absolute_url = urljoin(base_url, href) if base_url else href
    absolute_url = urldefrag(absolute_url).url
    parsed = urlparse(absolute_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return absolute_url


def _domain_from_url(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def _blocked_search_response_reason(html: str, provider: str) -> str:
    text = _clean_text(html).lower()
    checks = (
        ("captcha", "CAPTCHA or bot challenge detected"),
        ("unusual traffic", "Unusual traffic challenge detected"),
        ("our systems have detected unusual traffic", "Unusual traffic challenge detected"),
        ("enable javascript", "Search provider requires JavaScript"),
        ("please enable javascript", "Search provider requires JavaScript"),
        ("verify you are a human", "Human verification challenge detected"),
        ("access denied", "Access denied by search provider"),
        ("temporarily blocked", "Temporarily blocked by search provider"),
    )
    for marker, reason in checks:
        if marker in text:
            return reason
    if provider == "google_html" and "/sorry/" in html.lower():
        return "Google sorry/CAPTCHA page detected"
    return ""


def _network_diagnostics() -> dict[str, Any]:
    dotenv_loaded = _load_dotenv_for_proxy()
    proxy_env_vars = [
        name
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        if os.environ.get(name)
    ]
    return {
        "http_client": "httpx",
        "trust_env": True,
        "follow_redirects": True,
        "dotenv_loaded": bool(dotenv_loaded),
        "proxy_env_vars": proxy_env_vars,
        "proxy_env_detected": bool(proxy_env_vars),
    }


def _network_error_message(exc: BaseException, diagnostics: dict[str, Any]) -> str:
    message = _redact_sensitive_urls(str(exc))
    if not message:
        message = type(exc).__name__
    proxy_hint = (
        f"detected proxy env vars: {', '.join(diagnostics.get('proxy_env_vars') or [])}"
        if diagnostics.get("proxy_env_detected")
        else "no HTTP_PROXY/HTTPS_PROXY/ALL_PROXY env vars detected"
    )
    return (
        f"{message}. Network/proxy failure while contacting Google/Bing; "
        f"{proxy_hint}. Check that the shell launching OpenPilot inherits your proxy settings."
    )


def _network_failure_warning(diagnostics: dict[str, Any]) -> str:
    proxy_hint = (
        f"Proxy env vars detected: {', '.join(diagnostics.get('proxy_env_vars') or [])}."
        if diagnostics.get("proxy_env_detected")
        else "No HTTP_PROXY/HTTPS_PROXY/ALL_PROXY env vars were detected."
    )
    return (
        "Search failed because every Google/Bing attempt was blocked, timed out, or hit a network error. "
        f"{proxy_hint} Verify the proxy in the shell that starts OpenPilot or add standard proxy vars to .env."
    )


def _redact_sensitive_urls(value: str) -> str:
    return re.sub(r"(?i)\b(?:https?|socks5?|socks)://[^\s'\"<>]+", "[redacted-url]", value)


def _is_network_exception(exc: BaseException) -> bool:
    return isinstance(exc, httpx.RequestError)


def _blocked_http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {403, 429, 503}:
        return int(status_code)
    return None


def _is_low_value_search_result_url(url: str) -> bool:
    if _is_search_engine_internal_url(url):
        return True
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    if not parsed.scheme.startswith("http") or not domain:
        return True
    low_value_domains = {
        "accounts.google.com",
        "login.live.com",
        "account.microsoft.com",
        "support.google.com",
        "policies.google.com",
    }
    if domain in low_value_domains:
        return True
    low_value_paths = ("/search", "/images", "/videos", "/maps", "/shopping", "/news", "/preferences")
    return path.startswith(low_value_paths)


def _dedupe_search_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    deduped = []
    for result in results:
        url = result.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(result)
    return deduped


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for link in links:
        url = link.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(link)
    return result


def _clamp_int(value: Any, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected integer value, got {value!r}") from exc
    return max(minimum, min(maximum, parsed))
