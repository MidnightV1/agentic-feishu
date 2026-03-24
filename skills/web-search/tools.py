# -*- coding: utf-8 -*-
"""Web search skill — Gemini grounding search for web and news."""

from __future__ import annotations

import logging
import os
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.skills.web_search")


async def _gemini_grounded_search(query: str, mode: str = "web") -> dict:
    """Use Gemini API with Google Search grounding to answer a query.

    Args:
        query: Search query
        mode: "web" or "news" (news prepends "latest news:" to bias results)
    """
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set in environment"}

    client = genai.Client(api_key=api_key)

    # For news, bias the query
    effective_query = f"latest news: {query}" if mode == "news" else query

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=effective_query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )

        # Extract grounding metadata
        result: dict[str, Any] = {"query": query, "mode": mode}

        if response.text:
            result["answer"] = response.text

        # Extract search sources from grounding metadata
        metadata = getattr(response.candidates[0], "grounding_metadata", None)
        if metadata:
            chunks = getattr(metadata, "grounding_chunks", [])
            sources = []
            for chunk in chunks[:10]:  # limit to top 10 sources
                web = getattr(chunk, "web", None)
                if web:
                    sources.append({
                        "title": getattr(web, "title", ""),
                        "url": getattr(web, "uri", ""),
                    })
            if sources:
                result["sources"] = sources

        return result

    except Exception as e:
        log.exception("Gemini grounded search failed")
        return {"error": f"{type(e).__name__}: {e}"}


@tool(
    summary="Web search: search_web, search_news (Gemini grounding)",
    deferred=True,
    description="""Web search using Gemini Google Search grounding.

Actions:
- search_web: General web search. params: {query: str}
  Returns an AI-synthesized answer with source URLs.
- search_news: News-focused search. params: {query: str}
  Same as search_web but biased toward recent news results.

The search uses Gemini's built-in Google Search grounding tool,
which provides real-time web results synthesized into a coherent answer.
""",
)
async def web_search(action: str, params: dict = {}) -> dict:
    """Dispatch web search operations.

    Args:
        action: One of: search_web, search_news
        params: Action-specific parameters
    """
    query = params.get("query", "")
    if not query:
        return {"error": "Missing param: query"}

    if action == "search_web":
        return await _gemini_grounded_search(query, mode="web")
    elif action == "search_news":
        return await _gemini_grounded_search(query, mode="news")
    else:
        return {
            "error": f"Unknown action '{action}'",
            "valid_actions": ["search_web", "search_news"],
        }
