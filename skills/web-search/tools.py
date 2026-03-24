# -*- coding: utf-8 -*-
"""Web search skill — Gemini grounding search for web and news.

CLI script for skill invocation. No @tool registration.

CLI usage:
    python3 skills/web-search/tools.py <action> --params '<json>'
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

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

        result: dict[str, Any] = {"query": query, "mode": mode}

        if response.text:
            result["answer"] = response.text

        metadata = getattr(response.candidates[0], "grounding_metadata", None)
        if metadata:
            chunks = getattr(metadata, "grounding_chunks", [])
            sources = []
            for chunk in chunks[:10]:
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


# -- CLI entry point ---------------------------------------------------------

async def _cli_main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Web search")
    parser.add_argument("action", choices=["search_web", "search_news"])
    parser.add_argument("--params", default="{}", help="JSON params")
    args = parser.parse_args()

    params = json.loads(args.params)
    result = await web_search(args.action, params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_cli_main())
