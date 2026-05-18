import csv
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from demo.data_process.data_fetch import run_download
from demo.data_process.data_quality import validate_daily_bar_quality
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, HistoryRequest
from vnpy.trader.utility import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TEST_START = datetime(1900, 1, 1)


def create_bar(trade_date: date, open_price: float, close_price: float) -> BarData:
    return BarData(
        gateway_name="TEST",
        symbol="510300",
        exchange=Exchange.SSE,
        interval=Interval.DAILY,
        datetime=datetime(
            trade_date.year, trade_date.month, trade_date.day, tzinfo=CHINA_TZ
        ),
        open_price=open_price,
        high_price=max(open_price, close_price),
        low_price=min(open_price, close_price),
        close_price=close_price,
        volume=100,
    )


def create_request() -> HistoryRequest:
    return HistoryRequest(
        symbol="510300",
        exchange=Exchange.SSE,
        start=DEFAULT_TEST_START,
        end=None,
        interval=Interval.DAILY,
    )


class MockDatafeed:
    def __init__(self, bars_by_symbol: dict[str, list[BarData]]) -> None:
        self.bars_by_symbol = bars_by_symbol

    def query_bar_history(self, req: HistoryRequest) -> list[BarData]:
        return self.bars_by_symbol.get(req.vt_symbol, [])


class MockDatabase:
    def __init__(self) -> None:
        self.saved: list[list[BarData]] = []

    def save_bar_data(self, bars: list[BarData]) -> bool:
        self.saved.append(bars)
        return True


class MockDatabaseWithPath(MockDatabase):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.db = type("Db", (), {"database": str(path)})()


class TestDataQuality(unittest.TestCase):
    def test_missing_trading_day_warns_but_allows_save(self) -> None:
        req: HistoryRequest = create_request()
        bars: list[BarData] = [
            create_bar(date(2025, 1, 2), 100, 101),
            create_bar(date(2025, 1, 4), 102, 103),
        ]
        trading_dates: list[date] = [
            date(2025, 1, 2),
            date(2025, 1, 3),
            date(2025, 1, 4),
        ]

        report = validate_daily_bar_quality(
            vt_symbol="510300.SSE",
            req=req,
            bars=bars,
            trading_dates=trading_dates,
        )

        self.assertTrue(report.can_save)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].issue_type, "missing_trading_day")
        self.assertEqual(report.issues[0].trade_date, date(2025, 1, 3))

    def test_price_jump_blocks_save(self) -> None:
        req: HistoryRequest = create_request()
        bars: list[BarData] = [
            create_bar(date(2025, 1, 2), 100, 100),
            create_bar(date(2025, 1, 3), 111, 112),
        ]
        trading_dates: list[date] = [
            date(2025, 1, 2),
            date(2025, 1, 3),
        ]

        report = validate_daily_bar_quality(
            vt_symbol="510300.SSE",
            req=req,
            bars=bars,
            trading_dates=trading_dates,
        )

        self.assertFalse(report.can_save)
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].issue_type, "price_jump")
        self.assertEqual(report.issues[0].prev_trade_date, date(2025, 1, 2))
        self.assertEqual(report.issues[0].trade_date, date(2025, 1, 3))

    def test_missing_day_gap_does_not_create_price_jump(self) -> None:
        req: HistoryRequest = create_request()
        bars: list[BarData] = [
            create_bar(date(2025, 1, 2), 100, 100),
            create_bar(date(2025, 1, 4), 120, 121),
        ]
        trading_dates: list[date] = [
            date(2025, 1, 2),
            date(2025, 1, 3),
            date(2025, 1, 4),
        ]

        report = validate_daily_bar_quality(
            vt_symbol="510300.SSE",
            req=req,
            bars=bars,
            trading_dates=trading_dates,
        )

        issue_types: set[str] = {issue.issue_type for issue in report.issues}

        self.assertTrue(report.can_save)
        self.assertEqual(issue_types, {"missing_trading_day"})

    def test_download_pipeline_saves_missing_day_and_blocks_price_jump(self) -> None:
        missing_only_bars: list[BarData] = [
            create_bar(date(2025, 1, 2), 100, 101),
            create_bar(date(2025, 1, 4), 102, 103),
        ]
        price_jump_bars: list[BarData] = [
            create_bar(date(2025, 1, 5), 100, 100),
            create_bar(date(2025, 1, 6), 112, 113),
        ]
        datafeed = MockDatafeed(
            {
                "510300.SSE": missing_only_bars,
                "510500.SSE": price_jump_bars,
            }
        )
        database = MockDatabase()

        def calendar_fetcher(start_date: date, end_date: date) -> list[date]:
            days: int = (end_date - start_date).days
            return [start_date + timedelta(days=i) for i in range(days + 1)]

        with tempfile.TemporaryDirectory() as tmpdir:
            report_path: Path = Path(tmpdir).joinpath("quality.csv")

            reports = run_download(
                vt_symbols=["510300.SSE", "510500.SSE"],
                start="",
                end="",
                datafeed=datafeed,
                database=database,
                calendar_fetcher=calendar_fetcher,
                report_path=report_path,
                output=lambda message: None,
            )

            with report_path.open(newline="", encoding="utf-8") as f:
                rows: list[dict[str, str]] = list(csv.DictReader(f))

        self.assertEqual(len(reports), 2)
        self.assertEqual(len(database.saved), 1)
        self.assertEqual(database.saved[0], missing_only_bars)
        self.assertEqual(
            {(row["vt_symbol"], row["issue_type"], row["action"]) for row in rows},
            {
                ("510300.SSE", "missing_trading_day", "warn_save"),
                ("510500.SSE", "price_jump", "block_save"),
            },
        )

    def test_download_pipeline_refuses_to_save_into_source_market_db(self) -> None:
        source_bars: list[BarData] = [
            create_bar(date(2025, 1, 2), 100, 101),
        ]
        datafeed = MockDatafeed({"510300.SSE": source_bars})

        def calendar_fetcher(start_date: date, end_date: date) -> list[date]:
            return [start_date]

        with tempfile.TemporaryDirectory() as tmpdir:
            source_path: Path = Path(tmpdir).joinpath("market.db")
            source_path.touch()
            database = MockDatabaseWithPath(source_path)
            messages: list[str] = []

            with patch("demo.data_process.data_fetch.MARKET_DB_PATH", source_path):
                reports = run_download(
                    vt_symbols=["510300.SSE"],
                    start="",
                    end="",
                    datafeed=datafeed,
                    database=database,
                    calendar_fetcher=calendar_fetcher,
                    report_path=Path(tmpdir).joinpath("quality.csv"),
                    output=messages.append,
                )

        self.assertEqual(reports, [])
        self.assertEqual(database.saved, [])
        self.assertTrue(any("源库相同" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
