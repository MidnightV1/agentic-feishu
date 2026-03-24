# -*- coding: utf-8 -*-
"""ArXiv tracker skill — paper search, topic tracking, summarization."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

from core.tool_registry import tool

log = logging.getLogger("agentic.skills.arxiv_tracker")

ARXIV_API = "https://export.arxiv.org/api/query"


async def _arxiv_search(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
    categories: list[str] | None = None,
) -> list[dict]:
    """Search arXiv API and parse Atom XML response.

    Args:
        query: Search terms
        max_results: Max papers to return
        sort_by: "relevance" or "lastUpdatedDate" or "submittedDate"
        categories: Optional list of arXiv categories to filter (e.g. ["cs.AI", "cs.CL"])
    """
    import httpx

    # Build query string
    search_query = query
    if categories:
        cat_filter = " OR ".join(f"cat:{c}" for c in categories)
        search_query = f"({query}) AND ({cat_filter})"

    sort_map = {
        "relevance": "relevance",
        "date": "lastUpdatedDate",
        "submitted": "submittedDate",
        "lastUpdatedDate": "lastUpdatedDate",
        "submittedDate": "submittedDate",
    }

    params = {
        "search_query": f"all:{search_query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_map.get(sort_by, "relevance"),
        "sortOrder": "descending",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(ARXIV_API, params=params)
        resp.raise_for_status()

    # Parse Atom XML
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(resp.text)

    papers = []
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        published_el = entry.find("atom:published", ns)
        updated_el = entry.find("atom:updated", ns)

        # Extract arXiv ID from entry id URL
        id_el = entry.find("atom:id", ns)
        arxiv_id = ""
        if id_el is not None and id_el.text:
            arxiv_id = id_el.text.split("/abs/")[-1]

        # Authors
        authors = []
        for author_el in entry.findall("atom:author", ns):
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # Categories
        cats = []
        for cat_el in entry.findall("atom:category", ns):
            term = cat_el.get("term", "")
            if term:
                cats.append(term)

        # PDF link
        pdf_url = ""
        for link_el in entry.findall("atom:link", ns):
            if link_el.get("title") == "pdf":
                pdf_url = link_el.get("href", "")

        paper = {
            "arxiv_id": arxiv_id,
            "title": (title_el.text or "").strip().replace("\n", " ") if title_el is not None else "",
            "authors": authors[:5],  # limit for readability
            "author_count": len(authors),
            "summary": (summary_el.text or "").strip()[:500] if summary_el is not None else "",
            "categories": cats,
            "published": (published_el.text or "")[:10] if published_el is not None else "",
            "updated": (updated_el.text or "")[:10] if updated_el is not None else "",
            "pdf_url": pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
            "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        }
        papers.append(paper)

    return papers


@tool(
    summary="ArXiv operations: search_papers, recent_papers, summarize_paper",
    deferred=True,
    description="""ArXiv paper search, tracking, and summarization.

Actions:
- search_papers: Search arXiv papers. params: {query: str, max_results?: int (default 10), categories?: list[str], sort_by?: "relevance"|"date"|"submitted"}
  Example categories: ["cs.AI", "cs.CL", "cs.LG", "cs.CV"]
- recent_papers: Get recent papers in specific categories. params: {categories: list[str], max_results?: int (default 10)}
  Fetches papers sorted by submission date.
- summarize_paper: Get detailed info for a specific paper. params: {arxiv_id: str}
  Returns full abstract and metadata for a single paper.

Common arXiv categories:
  cs.AI (AI), cs.CL (NLP), cs.LG (Machine Learning), cs.CV (Computer Vision),
  cs.MA (Multi-Agent), cs.SE (Software Engineering), stat.ML (Statistics ML)
""",
)
async def arxiv_tracker(action: str, params: dict = {}) -> dict:
    """Dispatch arXiv operations.

    Args:
        action: One of: search_papers, recent_papers, summarize_paper
        params: Action-specific parameters
    """
    if action == "search_papers":
        query = params.get("query", "")
        if not query:
            return {"error": "Missing param: query"}
        papers = await _arxiv_search(
            query=query,
            max_results=params.get("max_results", 10),
            categories=params.get("categories"),
            sort_by=params.get("sort_by", "relevance"),
        )
        return {"papers": papers, "count": len(papers), "query": query}

    elif action == "recent_papers":
        categories = params.get("categories", [])
        if not categories:
            return {"error": "Missing param: categories (list of arXiv category codes)"}
        # Search with wildcard query sorted by date
        papers = await _arxiv_search(
            query="*",
            max_results=params.get("max_results", 10),
            categories=categories,
            sort_by="submittedDate",
        )
        return {"papers": papers, "count": len(papers), "categories": categories}

    elif action == "summarize_paper":
        arxiv_id = params.get("arxiv_id", "")
        if not arxiv_id:
            return {"error": "Missing param: arxiv_id (e.g. '2401.12345')"}
        # Fetch single paper by ID
        import httpx

        url = f"{ARXIV_API}?id_list={quote_plus(arxiv_id)}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.text)
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {"error": f"Paper not found: {arxiv_id}"}

        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        published_el = entry.find("atom:published", ns)

        authors = []
        for a in entry.findall("atom:author", ns):
            n = a.find("atom:name", ns)
            if n is not None and n.text:
                authors.append(n.text.strip())

        cats = [c.get("term", "") for c in entry.findall("atom:category", ns) if c.get("term")]

        return {
            "arxiv_id": arxiv_id,
            "title": (title_el.text or "").strip().replace("\n", " ") if title_el is not None else "",
            "authors": authors,
            "abstract": (summary_el.text or "").strip() if summary_el is not None else "",
            "categories": cats,
            "published": (published_el.text or "")[:10] if published_el is not None else "",
            "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        }

    else:
        return {
            "error": f"Unknown action '{action}'",
            "valid_actions": ["search_papers", "recent_papers", "summarize_paper"],
        }
