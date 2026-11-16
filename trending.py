"""Mục "Đang nổi": bắt các repo tăng sao nhanh, kể cả repo không mới tạo.

GitHub không cho biết một repo tăng bao nhiêu sao mỗi ngày, nên mỗi sáng ta lưu số sao của
các repo ứng viên vào state/stars.json, sáng hôm sau trừ ra để có mức tăng.

Ứng viên lấy từ hai nguồn: danh sách repo trên toprepo.cuongit.net (chỉ lấy TÊN repo, mỗi ngày
đọc một lần, không gọi /api/ vì robots.txt của trang cấm) và các repo nhiều sao nhất theo
chủ đề trên GitHub. Số sao luôn lấy từ GitHub, không dựa vào số liệu của trang kia.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

SNAPSHOT_FILE = Path(__file__).parent / "state" / "stars.json"
KEEP_DAYS = 35        # giữ lịch sử số sao bấy nhiêu ngày
MIN_GROWTH = 50       # số sao tăng mỗi ngày tối thiểu để được coi là "đang nổi"
MAX_GAP_DAYS = 7      # mốc so sánh cũ hơn mức này thì bỏ (bản tin đã lâu không chạy)
TOPREPO_URL = "https://toprepo.cuongit.net/en"
TOPICS = ("claude-code", "claude-skills", "agent-skills", "mcp")
TOPIC_LIMIT = 30      # số repo nhiều sao nhất lấy cho mỗi chủ đề

_REPO_LINK = re.compile(r"/en/repos/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)")


def parse_toprepo_names(page):
    """Tên repo (chủ/tên) theo thứ hạng xuất hiện trên trang, đã bỏ trùng."""
    page = page.replace('\\"', '"')  # trang Next.js nhúng dữ liệu dạng chuỗi có dấu nháy thoát
    return list(dict.fromkeys(_REPO_LINK.findall(page)))


def load_snapshots(path=SNAPSHOT_FILE):
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def with_today(snapshots, today, stars):
    """Bản sao của lịch sử kèm số sao hôm nay (chạy lại trong ngày thì ghi đè mục hôm nay)."""
    out = {d: dict(v) for d, v in snapshots.items()}
    out[today.isoformat()] = dict(stars)
    return out


def save_snapshots(snapshots, today, path=SNAPSHOT_FILE, keep_days=KEEP_DAYS):
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    kept = {d: v for d, v in snapshots.items() if d >= cutoff}
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(kept, indent=0, sort_keys=True) + "\n")


def baseline(snapshots, today, repo):
    """Số sao gần nhất TRƯỚC hôm nay của repo: (ngày, số sao), hoặc None nếu chưa có."""
    for d in sorted((d for d in snapshots if d < today.isoformat()), reverse=True):
        if repo in snapshots[d]:
            day = date.fromisoformat(d)
            return (day, snapshots[d][repo]) if (today - day).days <= MAX_GAP_DAYS else None
    return None


def has_baseline(stars_now, snapshots, today):
    return any(baseline(snapshots, today, repo) for repo in stars_now)


def rank_rising(stars_now, snapshots, today, limit=10, min_growth=MIN_GROWTH):
    """Repo tăng sao nhanh nhất (tính theo số sao tăng mỗi ngày), bỏ repo chưa có mốc so sánh."""
    rows = []
    for repo, stars in stars_now.items():
        base = baseline(snapshots, today, repo)
        if not base:
            continue
        days = max((today - base[0]).days, 1)
        delta = stars - base[1]
        rate = delta / days
        if rate >= min_growth:
            rows.append({"name": repo, "stars": stars, "delta": delta, "days": days, "rate": rate})
    rows.sort(key=lambda r: r["rate"], reverse=True)
    return rows[:limit]
