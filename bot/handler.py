"""Xử lý một tin nhắn Telegram: kiểm tra người gửi, hiểu câu hỏi, tìm repo, trả lời."""

import hmac
import json
import os
import sys

from .gemini import gemini, understand
from .github import KIND_LABEL, search
from .telegram import send

GREETING = ("Chào bạn! Hỏi mình kiểu: 'tìm skill về luật', 'có plugin nào về SEO không' "
            "'MCP server cho database' hoặc 'mcp hay plugin nào về bảo mật'.")


def plain_list(repos):
    lines = ["Gemini đang bận nên mình gửi danh sách thô tìm được trên GitHub (chưa lọc theo chủ đề):"]
    for r in repos:
        old = " · lâu chưa cập nhật" if r["stale"] else ""
        lines.append(f"\n- {r['name']} (⭐ {r['stars']}, cập nhật {r['updated']}{old})\n"
                     f"  {r['description'] or '(chưa có mô tả)'}\n  {r['url']}")
    return "\n".join(lines)


def answer(question):
    try:
        kind, keywords = understand(question)
    except RuntimeError:
        # Gemini hỏng: nếu người dùng gõ sẵn từ khoá tiếng Anh ngắn thì dùng luôn
        words = question.split()
        if question.isascii() and 1 <= len(words) <= 3:
            kind, keywords = "skill", [w.lower() for w in words if w.replace("-", "").isalnum()][:2]
        else:
            raise
    if not keywords:
        return "Mình chưa hiểu bạn muốn tìm chủ đề gì. Bạn nói rõ hơn giúp mình nhé."
    repos = search(kind, keywords)
    if not repos:
        return (f"Mình tìm {KIND_LABEL[kind]} với từ khoá {', '.join(keywords)} "
                "mà chưa thấy repo nào phù hợp.")
    prompt = (
        "Bạn là trợ lý tìm tài nguyên Claude (skill, plugin, MCP server). Trả lời bằng tiếng Việt, ngắn gọn, tự nhiên. "
        "CHỈ được nhắc tới các repo trong danh sách dưới đây, không thêm repo nào khác và không bịa chức năng. "
        "Danh sách đã xếp theo mức nổi bật (nhiều sao, repo còn cập nhật đứng trước), hãy giữ nguyên thứ tự. "
        "Nếu người dùng hỏi kiểu có/không (ví dụ 'có skill X không'), câu đầu tiên phải trả lời thẳng "
        "'Có' hoặc 'Chưa thấy', rồi mới liệt kê. Chỉ trả lời 'Có' khi có repo mà mô tả thật sự khớp. "
        "Bỏ qua repo có mô tả không liên quan chủ đề; nếu không repo nào liên quan thì nói rõ là chưa thấy repo phù hợp. "
        "Mỗi repo giữ lại gồm một mục: tên repo, một câu nói nó làm gì (dựa vào mô tả, mô tả trống thì ghi rõ chưa có mô tả), "
        "số sao, ngày cập nhật cuối và link. Repo có stale=true thì ghi chú là lâu chưa cập nhật. "
        "Cuối cùng nhắc người dùng tự đọc README trước khi cài. Không dùng markdown, chỉ văn bản thường.\n\n"
        f"Câu hỏi: {question}\n\nDanh sách repo (JSON):\n{json.dumps(repos, ensure_ascii=False)}"
    )
    try:
        return gemini(prompt).strip()
    except RuntimeError as e:
        print(f"Gemini hỏng ở bước trả lời, gửi danh sách thô: {e}", file=sys.stderr)
        return plain_list(repos)


def handle_update(update, secret_header):
    """Trả về mã HTTP. Luôn 200 khi đã nhận (kể cả lỗi xử lý) để Telegram không gửi lại."""
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret or not hmac.compare_digest(secret_header or "", secret):
        return 403
    msg = update.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    allowed = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not text:
        return 200
    if chat_id != allowed:
        state = "chưa được đặt" if not allowed else "khác số này"
        print(f"Bỏ qua tin từ chat {chat_id}: TELEGRAM_CHAT_ID trên Vercel {state}", file=sys.stderr)
        return 200
    try:
        reply = GREETING if text.startswith("/start") else answer(text)
    except Exception as e:  # noqa: BLE001 - báo lỗi cho người dùng thay vì im lặng
        print(f"Lỗi xử lý: {e.__class__.__name__}: {e}", file=sys.stderr)
        if isinstance(e, RuntimeError) and str(e).startswith("Gemini"):
            reply = ("Gemini đang bận, hết lượt hoặc trả kết quả lạ nên mình chưa đọc được câu hỏi. "
                     "Bạn thử lại sau ít phút, hoặc gõ thẳng từ khoá tiếng Anh (ví dụ: security nodejs).")
        else:
            reply = "Mình gặp lỗi khi tìm, bạn thử lại sau ít phút nhé."
    try:
        send(chat_id, reply)
    except OSError as e:
        print(f"Gửi Telegram lỗi: {e}", file=sys.stderr)
    return 200
