import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import HistoryRequest
from vnpy.trader.utility import ZoneInfo

from vnpy_localdata.localdata_datafeed import (
    LocaldataDatafeed,
    query_local_open_dates,
)


CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TEST_START = datetime(1900, 1, 1)


def create_request(
    symbol: str = "510300",
    exchange: Exchange = Exchange.SSE,
    start: datetime | None = None,
    end: datetime | None = None,
    interval: Interval = Interval.DAILY,
) -> HistoryRequest:
    return HistoryRequest(
        symbol=symbol,
        exchange=exchange,
        start=start or DEFAULT_TEST_START,
        end=end,
        interval=interval,
    )


class TempMarketDb:
    def __enter__(self) -> "TempMarketDb":
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name).joinpath("market.db")
        with sqlite3.connect(self.path) as conn:
            create_market_schema(conn)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.tmpdir.cleanup()

    def insert_bar(
        self,
        table: str,
        symbol: str,
        trade_date: str,
        open_price: float,
        exchange: str = "SSE",
        interval: str = "1d",
        volume: float = 100,
        amount: float = 200,
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                f"""
                INSERT INTO {table} (
                    symbol, exchange, datetime, interval, open, high, low, close,
                    pre_close, change, pct_chg, vol, amount, provider, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    exchange,
                    f"{trade_date} 00:00:00",
                    interval,
                    open_price,
                    open_price + 2,
                    open_price - 1,
                    open_price + 1,
                    open_price - 0.5,
                    1,
                    1,
                    volume,
                    amount,
                    "test",
                    "2026-05-18 00:00:00",
                ),
            )

    def insert_factor(
        self,
        table: str,
        symbol: str,
        trade_date: str,
        adj_factor: float,
        exchange: str = "SSE",
        interval: str = "1d",
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                f"""
                INSERT INTO {table} (
                    symbol, exchange, datetime, interval, adj_factor, provider, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    exchange,
                    f"{trade_date} 00:00:00",
                    interval,
                    adj_factor,
                    "test",
                    "2026-05-18 00:00:00",
                ),
            )


def create_market_schema(conn: sqlite3.Connection) -> None:
    for table in ("fund_daily_bars", "stock_daily_bars", "index_daily_bars"):
        conn.execute(
            f"""
            CREATE TABLE {table} (
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                datetime TEXT NOT NULL,
                interval TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                pre_close REAL,
                change REAL,
                pct_chg REAL,
                vol REAL,
                amount REAL,
                provider TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, exchange, datetime, interval)
            )
            """
        )

    for table in ("fund_adj_factors", "stock_adj_factors"):
        conn.execute(
            f"""
            CREATE TABLE {table} (
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                datetime TEXT NOT NULL,
                interval TEXT NOT NULL,
                adj_factor REAL,
                provider TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, exchange, datetime, interval)
            )
            """
        )


class TestLocaldataDatafeed(unittest.TestCase):
    def test_qfq_uses_query_end_factor_as_denominator(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("fund_daily_bars", "510300", "2025-01-02", 10)
            db.insert_bar("fund_daily_bars", "510300", "2025-01-03", 20)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-02", 1)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-03", 2)

            bars = LocaldataDatafeed().query_bar_history(
                create_request(
                    start=datetime(2025, 1, 2),
                    end=datetime(2025, 1, 3),
                ),
                output=lambda message: None,
            )

        self.assertEqual([bar.open_price for bar in bars], [5.0, 20.0])
        self.assertEqual([bar.high_price for bar in bars], [6.0, 22.0])
        self.assertEqual([bar.low_price for bar in bars], [4.5, 19.0])
        self.assertEqual([bar.close_price for bar in bars], [5.5, 21.0])

    def test_qfq_uses_last_returned_bar_factor_when_end_is_empty(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("fund_daily_bars", "510300", "2025-01-02", 10)
            db.insert_bar("fund_daily_bars", "510300", "2025-01-03", 20)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-02", 1)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-03", 4)

            bars = LocaldataDatafeed().query_bar_history(
                create_request(start=datetime(2025, 1, 2), end=None),
                output=lambda message: None,
            )

        self.assertEqual([bar.open_price for bar in bars], [2.5, 20.0])

    def test_qfq_rounds_prices_to_tushare_two_decimals(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("fund_daily_bars", "510300", "2025-01-02", 1.234)
            db.insert_bar("fund_daily_bars", "510300", "2025-01-03", 2)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-02", 1)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-03", 2)

            bars = LocaldataDatafeed().query_bar_history(
                create_request(
                    start=datetime(2025, 1, 2),
                    end=datetime(2025, 1, 3),
                ),
                output=lambda message: None,
            )

        self.assertEqual(bars[0].open_price, 0.62)
        self.assertEqual(bars[0].high_price, 1.62)
        self.assertEqual(bars[0].low_price, 0.12)
        self.assertEqual(bars[0].close_price, 1.12)

    def test_qfq_uses_last_trading_day_factor_when_end_is_not_trading_day(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("fund_daily_bars", "510300", "2025-01-02", 10)
            db.insert_bar("fund_daily_bars", "510300", "2025-01-03", 20)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-02", 1)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-03", 5)

            bars = LocaldataDatafeed().query_bar_history(
                create_request(
                    start=datetime(2025, 1, 2),
                    end=datetime(2025, 1, 4),
                ),
                output=lambda message: None,
            )

        self.assertEqual([bar.open_price for bar in bars], [2.0, 20.0])

    def test_skips_when_adjust_factor_is_missing_or_incomplete(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("fund_daily_bars", "510300", "2025-01-02", 10)
            db.insert_bar("fund_daily_bars", "510300", "2025-01-03", 20)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-02", 1)
            messages: list[str] = []

            bars = LocaldataDatafeed().query_bar_history(
                create_request(),
                output=messages.append,
            )

        self.assertEqual(bars, [])
        self.assertTrue(any("复权因子覆盖不足" in message for message in messages))

    def test_skips_non_daily_request(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("fund_daily_bars", "510300", "2025-01-02", 10)
            db.insert_factor("fund_adj_factors", "510300", "2025-01-02", 1)
            messages: list[str] = []

            bars = LocaldataDatafeed().query_bar_history(
                create_request(interval=Interval.MINUTE),
                output=messages.append,
            )

        self.assertEqual(bars, [])
        self.assertTrue(any("仅支持日线" in message for message in messages))

    def test_routes_fund_before_stock_and_maps_bar_fields(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("stock_daily_bars", "510300", "2025-01-02", 30)
            db.insert_factor("stock_adj_factors", "510300", "2025-01-02", 1)
            db.insert_bar(
                "fund_daily_bars",
                "510300",
                "2025-01-02",
                10,
                volume=123,
                amount=456,
            )
            db.insert_factor("fund_adj_factors", "510300", "2025-01-02", 1)

            bars = LocaldataDatafeed().query_bar_history(
                create_request(),
                output=lambda message: None,
            )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].open_price, 10.0)
        self.assertEqual(bars[0].volume, 123)
        self.assertEqual(bars[0].turnover, 456)
        self.assertEqual(bars[0].open_interest, 0)
        self.assertEqual(bars[0].gateway_name, "LOCAL")
        self.assertEqual(bars[0].datetime.tzinfo, CHINA_TZ)

    def test_routes_to_stock_when_fund_data_is_absent(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar(
                "stock_daily_bars", "000001", "2025-01-02", 30, exchange="SZSE"
            )
            db.insert_factor(
                "stock_adj_factors", "000001", "2025-01-02", 1, exchange="SZSE"
            )

            bars = LocaldataDatafeed().query_bar_history(
                create_request(symbol="000001", exchange=Exchange.SZSE),
                output=lambda message: None,
            )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].open_price, 30.0)
        self.assertEqual(bars[0].exchange, Exchange.SZSE)

    def test_routes_to_stock_when_fund_factor_coverage_is_incomplete(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar(
                "fund_daily_bars", "000001", "2025-01-02", 10, exchange="SZSE"
            )
            db.insert_bar(
                "fund_daily_bars", "000001", "2025-01-03", 20, exchange="SZSE"
            )
            db.insert_factor(
                "fund_adj_factors", "000001", "2025-01-02", 1, exchange="SZSE"
            )
            db.insert_bar(
                "stock_daily_bars", "000001", "2025-01-02", 30, exchange="SZSE"
            )
            db.insert_factor(
                "stock_adj_factors", "000001", "2025-01-02", 1, exchange="SZSE"
            )
            messages: list[str] = []

            bars = LocaldataDatafeed().query_bar_history(
                create_request(symbol="000001", exchange=Exchange.SZSE),
                output=messages.append,
            )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].open_price, 30.0)
        self.assertTrue(any("fund_daily_bars" in message for message in messages))

    def test_local_calendar_reads_distinct_dates_from_daily_tables(self) -> None:
        with (
            TempMarketDb() as db,
            patch("vnpy_localdata.localdata_datafeed.MARKET_DB_PATH", db.path),
        ):
            db.insert_bar("fund_daily_bars", "510300", "2025-01-02", 10)
            db.insert_bar(
                "stock_daily_bars", "000001", "2025-01-03", 20, exchange="SZSE"
            )
            db.insert_bar("index_daily_bars", "000300", "2025-01-04", 30)

            dates = query_local_open_dates(
                start_date=datetime(2025, 1, 1).date(),
                end_date=datetime(2025, 1, 4).date(),
            )

        self.assertEqual(
            [trade_date.isoformat() for trade_date in dates],
            ["2025-01-02", "2025-01-03", "2025-01-04"],
        )


if __name__ == "__main__":
    unittest.main()
