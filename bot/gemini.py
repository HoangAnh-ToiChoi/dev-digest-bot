"""Gọi Gemini và dùng nó để hiểu câu hỏi của người dùng.

Gói free của Gemini hay quá tải (503/429), nên có thử lại và model dự phòng.
"""

import json
import os
import time
import urllib.error

from .net import http_json

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
RETRY_CODES = (429, 500, 503)
BUDGET_SECONDS = 35  # tổng thời gian chờ một lần gọi, để Telegram không gửi lại tin
KINDS = ("skill", "plugin", "mcp", "any")  # "any": người dùng hỏi nhiều loại cùng lúc hoặc không rõ
UNDERSTAND_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "kind": {"type": "STRING"},  # giá trị hợp lệ được code kiểm tra lại ở understand()
        "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["kind", "keywords"],
}


def gemini(prompt, as_json=False, schema=None):
    models = []
    for m in (os.environ.get("GEMINI_MODEL"), "gemini-flash-latest",
              "gemini-flash-lite-latest", "gemini-3.1-flash-lite"):
        if m and m not in models:
            models.append(m)
    config = {"responseMimeType": "application/json"} if as_json else {}
    if schema:
        config["responseSchema"] = schema
    errors = []
    deadline = time.time() + BUDGET_SECONDS
    for model in models:
        for wait in (0, 3):
            if time.time() + wait > deadline:
                break
            time.sleep(wait)
            try:
                data = http_json(
                    GEMINI_URL.format(model),
                    {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
                    {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                    timeout=12,
                )
                cand = data["candidates"][0]
                if cand.get("finishReason") != "STOP":
                    errors.append(f"{model}: finishReason={cand.get('finishReason')}")
                    break
                return "".join(p.get("text", "") for p in cand["content"]["parts"])
            except urllib.error.HTTPError as e:
                errors.append(f"{model}: HTTP {e.code}")
                if e.code not in RETRY_CODES:
                    break  # key sai, model không tồn tại...: sang model khác
            except (OSError, KeyError, IndexError, ValueError) as e:
                errors.append(f"{model}: {e.__class__.__name__}")
    raise RuntimeError("Gemini lỗi (" + "; ".join(errors) + ")")


def understand(question):
    """Trả về (loại, từ khoá). Loại: skill, plugin, mcp hoặc any. Từ khoá: tối đa 2 từ tiếng Anh."""
    prompt = (
        "Người dùng hỏi tìm tài nguyên cho Claude trên GitHub. Trả về JSON "
        '{"kind": "...", "keywords": ["..."]}. '
        'kind là "skill" (Claude skill, agent skill), "plugin" (Claude Code plugin hoặc marketplace) '
        'hoặc "mcp" (MCP server). Người dùng hỏi nhiều loại cùng lúc (ví dụ "mcp hay plugin") hoặc không rõ thì dùng "any". '
        "keywords gồm tối đa 2 từ tiếng Anh, mỗi từ đúng một từ, mô tả CHỦ ĐỀ (ví dụ: legal, law, finance, seo). "
        'Không đưa các từ "claude", "skill", "plugin", "mcp", "server", "github" vào keywords.\n\n'
        "Câu hỏi: " + question
    )
    try:
        data = json.loads(gemini(prompt, as_json=True, schema=UNDERSTAND_SCHEMA))
    except ValueError as e:
        raise RuntimeError("Gemini trả JSON không đọc được") from e
    if isinstance(data, list) and data:
        data = data[0]  # Gemini đôi khi bọc kết quả trong mảng
    if not isinstance(data, dict):
        raise RuntimeError(f"Gemini trả JSON lạ ({type(data).__name__})")
    kind = data.get("kind") if data.get("kind") in KINDS else "skill"
    raw = data.get("keywords") if isinstance(data.get("keywords"), list) else []
    words = [w.strip().lower() for w in raw if isinstance(w, str) and w.strip()]
    return kind, [w for w in words if w.replace("-", "").isalnum()][:2]
