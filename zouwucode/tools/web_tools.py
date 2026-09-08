"""Web search and fetch tools."""

from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BaseTool, ToolSpec, ToolResult


async def _check_network(sandbox) -> Optional[str]:
    """Return an error message when sandbox denies network access, else None."""
    checker = getattr(sandbox, "check_network", None) if sandbox else None
    if checker is None:
        return None
    if not await checker(""):
        return "Network access is disabled by sandbox config."
    return None


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
        try:
            denied = await _check_network(self.sandbox)
            if denied:
                return ToolResult(success=False, output="", error=denied)

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for item in soup.select(".result")[:count]:
                    title_el = item.select_one(".result__title a")
                    snippet_el = item.select_one(".result__snippet")
                    if title_el:
                        title = title_el.get_text(strip=True)
                        url = title_el.get("href", "")
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                        results.append(f"• [{title}]({url})\n  {snippet}")

                if not results:
                    return ToolResult(success=True, output="No results found.")

                return ToolResult(success=True, output="\n\n".join(results))

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


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
            return ToolResult(success=False, output="", error=str(e))