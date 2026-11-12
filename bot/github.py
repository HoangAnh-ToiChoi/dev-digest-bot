"""Tìm repo trên GitHub và xếp hạng: nhiều sao trước, nhưng repo còn được cập nhật đứng trên repo bỏ hoang."""

import os
import urllib.parse
from datetime import datetime, timedelta, timezone

from .net import http_json

MAX_REPOS = 6
PER_QUERY = 10
FRESH_DAYS = 365  # lâu hơn mức này không có commit thì coi là lâu chưa cập nhật

# Skill và plugin hay nằm chung một repo, nên hỏi loại nào cũng tìm cả hai
QUERIES = {
    "skill": ["claude skill {kw}", "claude plugin {kw}"],
    "plugin": ["claude plugin {kw}", "claude skill {kw}"],
    "mcp": ["mcp server {kw}"],
}
KIND_LABEL = {"skill": "skill / plugin", "plugin": "plugin / skill", "mcp": "MCP server"}


def _search_one(query, headers):
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": f"{query} in:name,description,topics", "sort": "stars", "order": "desc", "per_page": PER_QUERY})
    return http_json(url, headers=headers, timeout=15).get("items", [])


def rank(repos):
    """Repo còn cập nhật xếp trước, trong mỗi nhóm thì nhiều sao xếp trước."""
    return sorted(repos, key=lambda r: (r["stale"], -r["stars"]))


def search(kind, keywords):
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
    found, last_error = {}, None
    for kw in keywords:
        for template in QUERIES.get(kind, QUERIES["skill"]):
            try:
                items = _search_one(template.format(kw=kw), headers)
            except OSError as e:  # một truy vấn lỗi (ví dụ hết lượt) thì vẫn dùng kết quả các truy vấn khác
                last_error = e
                continue
            for r in items:
                found.setdefault(r["full_name"], {
                    "name": r["full_name"],
                    "url": r["html_url"],
                    "description": (r.get("description") or "")[:200],
                    "stars": r["stargazers_count"],
                    "updated": r["pushed_at"][:10],
                    "stale": r["pushed_at"][:10] < cutoff,
                })
    if not found and last_error:
        raise last_error
    return rank(found.values())[:MAX_REPOS]
