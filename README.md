# Dev Digest Bot

Mỗi sáng 8h, bot gửi một tin tổng hợp về Telegram, gồm 6 mục:

| Mục | Số tin tối đa | Nguồn |
|---|---|---|
| 🔥 Repo hot | 5 | GitHub Search API: repo tạo trong 7 ngày, nhiều sao |
| 🧩 Claude skill mới | 3 | GitHub Search API: repo có "claude skill" |
| 🧠 Model / release AI | 4 | RSS chính thức (OpenAI, Google, DeepMind, Hugging Face) và model trending trên Hugging Face |
| 🛠️ Cách dùng AI / workflow | 3 | Simon Willison, Latent Space |
| 📦 Framework / thư viện | 3 | GitHub Releases của các repo trong `WATCH_REPOS` (chỉ bản major/minor) |
| 🌐 Xu hướng ngành IT | 3 | Hacker News, bài trên 200 điểm |

Mỗi sáng thứ Hai, bot gửi thêm link bảng xếp hạng model [arena.ai](https://arena.ai/leaderboard).

Mỗi tin có link bấm vào là tới thẳng trang gốc.

## Cách bot tránh sót tin và tin sai

- **Không gửi trùng:** tin đã gửi được lưu vào `state/sent.json` trong 30 ngày.
- **Mục nào thiếu tin thì tìm rộng hơn:** khung thời gian tăng từ 24h lên 48h, và tin đó được ghi chú "tin 2 ngày".
- **Báo nguồn lỗi:** nếu một nguồn không tải được, cuối tin sẽ có dòng "⚠️ Nguồn lỗi hôm nay", để bạn biết hôm đó có thể sót tin.
- **Lọc repo có dấu hiệu sao ảo:** bỏ repo có trên 1000 sao nhưng số fork dưới 0,5% số sao.
- **Model chỉ lấy từ nguồn chính thức:** không lấy từ các trang tổng hợp, vì các trang đó hay ghi sai tên và ngày.

## Cài đặt

1. Tạo một repo GitHub mới, ví dụ `dev-digest-bot`, rồi copy **toàn bộ nội dung thư mục này** vào gốc repo. Thư mục `.github/workflows` phải nằm ở gốc repo.
2. Tạo bot Telegram:
   - Nhắn `/newbot` cho @BotFather để lấy **token**.
   - Nhắn một tin bất kỳ cho bot vừa tạo, rồi mở `https://api.telegram.org/bot<TOKEN>/getUpdates` để lấy **chat_id**.
3. Trong repo, vào **Settings → Secrets and variables → Actions → New repository secret** và thêm:
   - `TELEGRAM_TOKEN` (bắt buộc)
   - `TELEGRAM_CHAT_ID` (bắt buộc)
   - `ANTHROPIC_API_KEY` (không bắt buộc): bật tóm tắt mỗi tin một câu tiếng Việt.
4. Vào **Settings → Actions → General → Workflow permissions** và chọn **Read and write permissions**. Bot cần quyền này để lưu `state/sent.json`.
5. Vào tab **Actions**, chọn **Dev Digest to Telegram** rồi bấm **Run workflow** để thử ngay.

Không cần key GitHub, vì GitHub Actions tự cấp `GITHUB_TOKEN`.

## Tóm tắt "Là gì / Vì sao hot" bằng Claude (không bắt buộc)

Khi có secret `ANTHROPIC_API_KEY`, dưới mỗi tin sẽ có thêm 2 dòng:

```
• deepseek-ai/dsh-superagent-prototype – Towards a More Autonomous Agent System (⭐ 5,861)
   📝 Là gì: ...
   🔥 Vì sao đáng chú ý: ...
```

- Bot gửi cho Claude cả README của repo, model card trên Hugging Face, ghi chú phát hành và đoạn trích bài viết, nên phần tóm tắt dựa trên nội dung thật chứ không chỉ dựa vào tiêu đề.
- Claude cũng loại các tin không liên quan tới IT/AI. Ví dụ: chuyện nông nghiệp trên Hacker News, hoặc bản đóng gói lại của model có sẵn. Mỗi mục lấy dư 2 tin để bù cho tin bị loại.
- Model mặc định là `claude-opus-5-5`, ở mức effort `low`, tốn khoảng 0,1 USD mỗi ngày. Muốn rẻ hơn nhiều, vào **Settings → Secrets and variables → Actions → Variables** và đặt `CLAUDE_MODEL` = `claude-haiku-5-5`, chưa tới 0,01 USD mỗi ngày.
- Nếu Claude lỗi, bot vẫn gửi tin như bình thường, chỉ không có tóm tắt.
- Tin trên Hacker News chỉ có tiêu đề và số điểm, nên phần "vì sao" của mục này có thể ngắn hơn các mục khác.

## Chạy thử trên máy

```bash
pip install -r requirements.txt
```

```bash
DRY_RUN=1 python dev_digest.py
```

`DRY_RUN=1` chỉ in tin ra màn hình. Nó không gửi Telegram và không ghi `state/sent.json`.

## Tuỳ chỉnh

Mọi thứ nằm ở đầu file `dev_digest.py`:

- `WATCH_REPOS`: các framework cần theo dõi. Mặc định theo stack của SchoolarsGo: React, Vite, Tailwind, Express, Supabase, Node, Playwright, Zod, TanStack Query, cùng Next.js, Bun và Claude Code.
- `MODEL_FEEDS`, `WORKFLOW_FEEDS`: thêm hoặc bớt nguồn RSS.
- `SECTIONS`: đổi số tin tối đa của từng mục.
- Giờ gửi: sửa `cron` trong `.github/workflows/dev-digest.yml`. Giờ ở đây là UTC, tức giờ Việt Nam trừ 7.

## Lưu ý

- GitHub có thể chạy trễ 5–30 phút vào giờ cao điểm.
- Anthropic không có RSS chính thức. Tin về Claude chủ yếu sẽ đến từ Hacker News và GitHub.
- Bot commit `state/sent.json` mỗi ngày, nên repo luôn có hoạt động. Nhờ vậy, GitHub không tự tắt lịch chạy sau 60 ngày.
