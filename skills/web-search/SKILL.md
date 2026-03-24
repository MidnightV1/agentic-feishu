---
name: web-search
description: 网络搜索（Gemini Google Search grounding）。搜索网页和新闻，返回 AI 合成答案和来源链接。
---

# Web Search

使用 Gemini Google Search grounding 进行实时网络搜索。

## 调用方式

```bash
cd ~/Agent\ Space/agentic-feishu && python3 skills/web-search/tools.py <action> --params '<json>'
```

## Actions

- **search_web** — 通用网络搜索。params: `{query: str}`
  返回 AI 合成答案 + 来源 URL 列表。
- **search_news** — 新闻搜索。params: `{query: str}`
  同 search_web 但偏向近期新闻结果。

## 环境要求

需要 `GEMINI_API_KEY` 环境变量。

## Examples

```bash
python3 skills/web-search/tools.py search_web --params '{"query": "latest AI news"}'
python3 skills/web-search/tools.py search_news --params '{"query": "OpenAI GPT-5"}'
```
