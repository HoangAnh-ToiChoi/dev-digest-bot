"""Kiểm thử mục "Đang nổi". Chạy: python -m unittest discover -s tests (cần cài requirements.txt)."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_digest as dd  # noqa: E402
import trending as tr  # noqa: E402

TODAY = date(2026, 11, 16)


def repo(name, stars, forks=None, desc="mô tả", org=False):
    owner = name.split("/")[0]
    return {"full_name": name, "html_url": f"https://github.com/{name}", "description": desc,
            "stargazers_count": stars, "forks_count": stars // 5 if forks is None else forks,
            "created_at": "2026-01-18T00:00:00Z", "owner": {"login": owner, "type": "Organization" if org else "User"}}


class ParseAndSnapshots(unittest.TestCase):
    def test_parse_names_keeps_rank_and_dedupes(self):
        page = r'<a href=\"/en/repos/morluto/rea\">x</a> "href":"/en/repos/affaan-m/ECC" /en/repos/morluto/rea/ /en/repos/a.b/c-d_e'
        self.assertEqual(tr.parse_toprepo_names(page), ["morluto/rea", "affaan-m/ECC", "a.b/c-d_e"])
        self.assertEqual(tr.parse_toprepo_names("không có gì"), [])

    def test_rank_rising(self):
        snaps = {"2026-11-15": {"a/big": 1000, "b/slow": 500, "c/shrink": 900, "d/new": 10}}
        now = {"a/big": 1814, "b/slow": 520, "c/shrink": 800, "e/no-baseline": 99999, "d/new": 400}
        rows = tr.rank_rising(now, snaps, TODAY)
        self.assertEqual([r["name"] for r in rows], ["a/big", "d/new"])  # 814/ngày, 390/ngày; slow < 50; không baseline bị bỏ
        self.assertEqual((rows[0]["delta"], rows[0]["days"]), (814, 1))

    def test_rate_uses_gap_and_ignores_stale_baseline(self):
        snaps = {"2026-11-13": {"a/x": 1000}, "2026-11-01": {"b/y": 1}}
        rows = tr.rank_rising({"a/x": 1300, "b/y": 5000}, snaps, TODAY)
        self.assertEqual([(r["name"], r["days"], round(r["rate"])) for r in rows], [("a/x", 3, 100)])  # b/y: mốc quá 7 ngày

    def test_baseline_uses_latest_earlier_snapshot_and_skips_today(self):
        snaps = {"2026-11-14": {"a/x": 1}, "2026-11-15": {"b/y": 2}, "2026-11-16": {"a/x": 999}}
        self.assertEqual(tr.baseline(snaps, TODAY, "a/x"), (date(2026, 11, 14), 1))
        self.assertIsNone(tr.baseline(snaps, TODAY, "zzz"))

    def test_save_load_and_prune(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state" / "stars.json"
            snaps = {"2026-01-01": {"old/repo": 1}, "2026-11-15": {"a/x": 5}}
            snaps = tr.with_today(snaps, TODAY, {"a/x": 9})
            tr.save_snapshots(snaps, TODAY, path)
            loaded = tr.load_snapshots(path)
            self.assertEqual(loaded, {"2026-11-15": {"a/x": 5}, "2026-11-16": {"a/x": 9}})  # mốc quá 35 ngày bị cắt
            self.assertEqual(tr.load_snapshots(Path(d) / "khong-co.json"), {})
            path.write_text("{hỏng")
            self.assertEqual(tr.load_snapshots(path), {})


class FetchRising(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(dd, "datetime")
        mocked = patcher.start()
        mocked.now.return_value.date.return_value = TODAY
        self.addCleanup(patcher.stop)
        self.snaps = {}
        p = mock.patch.object(tr, "load_snapshots", side_effect=lambda *a, **k: self.snaps)
        p.start(); self.addCleanup(p.stop)
        self.topic_repos = {"claude-code": [repo("t/one", 5000), repo("t/farm", 5000, forks=0)], "mcp": [repo("t/two", 800)]}
        self.page = '/en/repos/top/first /en/repos/t/one /en/repos/top/second'
        self.api = {"top/first": repo("top/first", 300000), "top/second": repo("top/second", 2000)}

    def run_fetch(self, page_ok=True, topics_ok=True):
        def gh_get(path, **params):
            if path == "/search/repositories":
                if not topics_ok:
                    raise dd.requests.RequestException("boom")
                return {"items": self.topic_repos.get(params["q"].split(":")[1], [])}
            name = path.removeprefix("/repos/")
            if name not in self.api:
                raise dd.requests.RequestException("404")
            return self.api[name]

        def get(url, **kw):
            if not page_ok:
                raise dd.requests.RequestException("down")
            resp = mock.Mock(text=self.page); resp.raise_for_status = lambda: None
            return resp

        with mock.patch.object(dd, "gh_get", gh_get), mock.patch.object(dd.requests, "get", get):
            return dd.fetch_rising(24)

    def test_first_day_falls_back_to_toprepo_rank(self):
        items, soft = self.run_fetch()
        self.assertEqual([i["ref"][1] for i in items], ["top/first", "t/one", "top/second"])  # đúng thứ tự toprepo
        self.assertIn("hạng #1 trên toprepo", items[0]["note"])
        self.assertNotIn("t/farm", dd.RISING["stars"])  # repo nghi sao ảo bị loại khỏi ứng viên
        self.assertEqual(soft, [])
        self.assertEqual(set(dd.RISING["stars"]), {"t/one", "t/two", "top/first", "top/second"})

    def test_next_day_ranks_by_growth(self):
        self.snaps = {"2026-11-15": {"t/one": 4000, "t/two": 790, "top/first": 299980, "top/second": 1500}}
        items, _ = self.run_fetch()
        self.assertEqual([i["ref"][1] for i in items], ["t/one", "top/second"])  # +1000/ngày, +500/ngày; còn lại < 50/ngày
        self.assertIn("+1,000 sao/ngày", items[0]["note"])
        self.assertIn("tăng +1,000 sao so với hôm qua", items[0]["context"])

    def test_still_works_when_toprepo_is_down_or_changed(self):
        self.snaps = {"2026-11-15": {"t/one": 4000}}
        items, soft = self.run_fetch(page_ok=False)
        self.assertEqual([i["ref"][1] for i in items], ["t/one"])
        self.assertTrue(any("toprepo" in s for s in soft))
        self.page = "trang đã đổi, không còn link repo"
        _, soft = self.run_fetch()
        self.assertTrue(any("không đọc được danh sách" in s for s in soft))

    def test_rate_limit_is_reported_and_stops_further_calls(self):
        self.api = {"top/first": repo("top/first", 300000)}
        calls = []

        def gh_get(path, **params):
            if path == "/search/repositories":
                return {"items": []}
            calls.append(path)
            err = dd.requests.HTTPError("403", response=mock.Mock(status_code=403))
            raise err

        resp = mock.Mock(text=self.page); resp.raise_for_status = lambda: None
        with mock.patch.object(dd, "gh_get", gh_get), mock.patch.object(dd.requests, "get", lambda *a, **k: resp):
            with self.assertRaises(RuntimeError):  # không còn ứng viên nào
                dd.fetch_rising(24)
        self.assertEqual(len(calls), 1)  # gặp 403 là dừng, không gọi tiếp

    def test_rate_limit_mid_way_keeps_what_was_collected(self):
        self.snaps = {"2026-11-15": {"t/one": 4000}}
        state = {"n": 0}

        def gh_get(path, **params):
            if path == "/search/repositories":
                return {"items": self.topic_repos.get(params["q"].split(":")[1], [])}
            state["n"] += 1
            raise dd.requests.HTTPError("429", response=mock.Mock(status_code=429))

        resp = mock.Mock(text=self.page); resp.raise_for_status = lambda: None
        with mock.patch.object(dd, "gh_get", gh_get), mock.patch.object(dd.requests, "get", lambda *a, **k: resp):
            items, soft = dd.fetch_rising(24)
        self.assertEqual([i["ref"][1] for i in items], ["t/one"])
        self.assertTrue(any("giới hạn lượt gọi" in s for s in soft))
        self.assertEqual(state["n"], 1)

    def test_no_candidates_raises(self):
        self.page = ""
        with self.assertRaises(RuntimeError):
            self.run_fetch(page_ok=False, topics_ok=False)


class WiringAndWorkflow(unittest.TestCase):
    def test_section_registered_after_repos(self):
        keys = [k for k, _, _ in dd.SECTIONS]
        self.assertEqual(keys[:2], ["repos", "rising"])
        self.assertIn("rising", dd.FETCHERS)

    def test_workflow_commits_star_snapshots_without_failing_when_missing(self):
        wf = (ROOT / ".github" / "workflows" / "dev-digest.yml").read_text()
        self.assertIn("git add state", wf)  # thêm cả thư mục: có stars.json thì lưu, thiếu thì không lỗi
        self.assertNotIn("git add state/sent.json", wf)

    def test_render_shows_rising_section(self):
        picked = {k: [] for k, _, _ in dd.SECTIONS}
        picked["rising"] = [dd.item("a/b – x", "https://github.com/a/b", "GitHub", "⭐ 1 · +100 sao/ngày")]
        text = "\n".join(dd.render(picked, []))
        self.assertIn("ĐANG NỔI", text)
        self.assertIn("a/b", text)


if __name__ == "__main__":
    unittest.main()
