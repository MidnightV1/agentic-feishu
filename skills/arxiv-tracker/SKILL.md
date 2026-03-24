---
name: arxiv-tracker
description: ArXiv 论文搜索、追踪与摘要。搜索论文、获取最新论文、查看论文详情。
---

# ArXiv Tracker

搜索和追踪 arXiv 学术论文。

## 调用方式

```bash
cd ~/Agent\ Space/agentic-feishu && python3 skills/arxiv-tracker/tools.py <action> --params '<json>'
```

## Actions

- **search_papers** — 搜索论文。params: `{query: str, max_results?: int (default 10), categories?: list[str], sort_by?: "relevance"|"date"|"submitted"}`
  常用 categories: `cs.AI`, `cs.CL`, `cs.LG`, `cs.CV`, `cs.MA`, `cs.SE`, `stat.ML`
- **recent_papers** — 获取指定分类的最新论文。params: `{categories: list[str], max_results?: int}`
- **summarize_paper** — 获取单篇论文详细信息。params: `{arxiv_id: str}`

## Examples

```bash
python3 skills/arxiv-tracker/tools.py search_papers --params '{"query": "multi-agent LLM", "categories": ["cs.AI", "cs.MA"]}'
python3 skills/arxiv-tracker/tools.py recent_papers --params '{"categories": ["cs.CL"], "max_results": 5}'
python3 skills/arxiv-tracker/tools.py summarize_paper --params '{"arxiv_id": "2401.12345"}'
```
