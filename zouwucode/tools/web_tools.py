"""Web search and fetch tools."""

from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseTool, ToolSpec, ToolResult


def _describe_exc(exc: Exception) -> str:
    """Human-readable error text — httpx ConnectTimeout/ReadTimeout have an
    EMPTY str(), so `str(e)` alone produced a blank error the model could not
    act on (and that looked like a silent hang in the UI)."""
    name = type(exc).__name__
    msg = str(exc).strip()
    if not msg:
        msg = {
            "ConnectTimeout": "连接超时（网络不可达或被防火墙拦截）",
            "ReadTimeout": "读取超时",
            "ConnectError": "无法建立连接",
        }.get(name, "无错误详情")
    return f"{name}: {msg}"


async def _check_network(sandbox) -> Optional[str]:
    """Return an error message when sandbox denies network access, else None."""
    checker = getattr(sandbox, "check_network", None) if sandbox else None
    if checker is None:
        return None
    if not await checker(""):
        return "Network access is disabled by sandbox config."
    return None


# Search engines tried in order. DuckDuckGo's html endpoint is unreachable
# from many mainland-China networks, so Bing (reachable there) is the
# primary and DDG the fallback — one dead engine no longer kills search.
_SEARCH_ENGINES = (
    ("bing", "https://www.bing.com/search"),
    ("duckduckgo", "https://html.duckduckgo.com/html/"),
)


def _parse_bing(html: str, count: int) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.b_algo")[:count]:
        a = li.select_one("h2 a")
        p = li.select_one(".b_caption p") or li.select_one("p")
        if a and a.get("href", "").startswith("http"):
            title = a.get_text(strip=True)
            snippet = p.get_text(strip=True) if p else ""
            out.append(f"• [{title}]({a['href']})\n  {snippet}")
    return out


def _parse_ddg(html: str, count: int) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select(".result")[:count]:
        title_el = item.select_one(".result__title a")
        snippet_el = item.select_one(".result__snippet")
        if title_el:
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            out.append(f"• [{title}]({url})\n  {snippet}")
    return out


_PARSERS = {"bing": _parse_bing, "duckduckgo": _parse_ddg}


class WebSearchTool(BaseTool):
    """Search the web for information."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description="Search the web for real-time information. Returns search results with snippets and URLs.",
            parameters={
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (max 10)",
                },
            },
            required=["query"],
        )

    async def execute(self, query: str, count: int = 5) -> ToolResult:
        count = max(1, min(int(count or 5), 10))
        denied = await _check_network(self.sandbox)
        if denied:
            return ToolResult(success=False, output="", error=denied)

        errors: list[str] = []
        for engine, url in _SEARCH_ENGINES:
            try:
                async with httpx.AsyncClient(timeout=12.0,
                                             follow_redirects=True) as client:
                    resp = await client.get(
                        url, params={"q": query},
                        headers={"User-Agent": "Mozilla/5.0 "
                                               "(Windows NT 10.0; Win64; x64)"},
                    )
                    resp.raise_for_status()
                results = _PARSERS[engine](resp.text, count)
                if results:
                    return ToolResult(success=True,
                                      output="\n\n".join(results))
                errors.append(f"{engine}: 无可解析结果")
            except Exception as exc:  # noqa: BLE001 — try next engine
                errors.append(f"{engine}: {_describe_exc(exc)}")

        return ToolResult(
            success=False, output="",
            error="Web search failed — " + "; ".join(errors)
                  + "。可改用 web_fetch 直接抓取已知 URL。")


class WebFetchTool(BaseTool):
    """Fetch content from a URL."""

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_fetch",
            description="Fetch content from a URL and return it as readable text.",
            parameters={
                "url": {
                    "type": "string",
                    "description": "URL to fetch",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum characters to return (default: 5000)",
                },
            },
            required=["url"],
        )

    async def execute(self, url: str, max_length: int = 5000) -> ToolResult:
        try:
            denied = await _check_network(self.sandbox)
            if denied:
                return ToolResult(success=False, output="", error=denied)

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "ZOUWUCODE/1.0"},
                )
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")

                # Remove script and style elements
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()

                text = soup.get_text(separator="\n", strip=True)
                text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

                if len(text) > max_length:
                    text = text[:max_length] + "\n\n...(truncated)"

                return ToolResult(success=True, output=text)

        except Exception as e:
            return ToolResult(success=False, output="", error=_describe_exc(e))