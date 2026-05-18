from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
import sqlite3
from urllib.parse import quote

from vnpy.trader.constant import Interval
from vnpy.trader.datafeed import BaseDatafeed
from vnpy.trader.object import BarData, HistoryRequest
from vnpy.trader.utility import ZoneInfo


MARKET_DB_PATH: Path = Path.home().joinpath(
    "Documents/0.InvestSystem/fin_data/data/market.db"
)

SOURCE_INTERVAL: str = "1d"
CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class TableRoute:
    bar_table: str
    factor_table: str


TABLE_ROUTES: tuple[TableRoute, ...] = (
    TableRoute("fund_daily_bars", "fund_adj_factors"),
    TableRoute("stock_daily_bars", "stock_adj_factors"),
)

CALENDAR_TABLES: tuple[str, ...] = (
    "fund_daily_bars",
    "stock_daily_bars",
    "index_daily_bars",
)


class LocaldataDatafeed(BaseDatafeed):
    """本地market.db日线数据服务。"""

    def __init__(self) -> None:
        self.inited: bool = False

    def init(self, output: Callable = print) -> bool:
        if not MARKET_DB_PATH.exists():
            output(f"LocalData数据服务初始化失败：源库不存在 {MARKET_DB_PATH.name}")
            return False

        self.inited = True
        return True

    def query_bar_history(
        self,
        req: HistoryRequest,
        output: Callable = print,
    ) -> list[BarData]:
        if req.interval != Interval.DAILY:
            output(f"LocalData仅支持日线请求：{req.vt_symbol}")
            return []

        if not self.inited and not self.init(output):
            return []

        try:
            with connect_readonly(MARKET_DB_PATH) as conn:
                return query_bars(conn, req, output)
        except (OSError, sqlite3.Error) as exc:
            output(f"LocalData查询失败：{req.vt_symbol}，{type(exc).__name__}")
            return []


def query_bars(
    conn: sqlite3.Connection,
    req: HistoryRequest,
    output: Callable,
) -> list[BarData]:
    conn.row_factory = sqlite3.Row
    matched_bar_route: bool = False

    for route in TABLE_ROUTES:
        bar_rows: list[sqlite3.Row] = query_bar_rows(conn, route.bar_table, req)
        if not bar_rows:
            continue

        matched_bar_route = True
        bar_dates: tuple[date, ...] = tuple(
            parse_local_datetime(row["datetime"]).date() for row in bar_rows
        )
        factor_by_date: dict[date, float] = query_factor_by_date(
            conn=conn,
            factor_table=route.factor_table,
            req=req,
        )
        missing_dates: tuple[date, ...] = tuple(
            trade_date for trade_date in bar_dates if trade_date not in factor_by_date
        )
        if missing_dates:
            output(
                "LocalData复权因子覆盖不足："
                f"{req.vt_symbol}，{route.bar_table}缺失{len(missing_dates)}天，跳过该表"
            )
            continue

        denominator_date: date = bar_dates[-1]
        denominator_factor: float | None = factor_by_date.get(denominator_date)
        if not denominator_factor:
            output(
                "LocalData复权因子无法对齐："
                f"{req.vt_symbol}，{route.factor_table}，跳过该表"
            )
            continue

        return [
            create_bar(
                req=req,
                row=row,
                adj_factor=factor_by_date[trade_date],
                denominator_factor=denominator_factor,
            )
            for row, trade_date in zip(bar_rows, bar_dates)
        ]

    if matched_bar_route:
        output(f"LocalData无完整前复权数据：{req.vt_symbol}")
    else:
        output(f"LocalData本地行情无数据：{req.vt_symbol}")
    return []


def query_bar_rows(
    conn: sqlite3.Connection,
    table: str,
    req: HistoryRequest,
) -> list[sqlite3.Row]:
    filters: list[str] = [
        "symbol = ?",
        "exchange = ?",
        "interval = ?",
    ]
    params: list[str] = [
        req.symbol,
        req.exchange.value,
        SOURCE_INTERVAL,
    ]

    append_date_filters(filters, params, req)

    sql: str = f"""
        SELECT datetime, open, high, low, close, pre_close, vol, amount
        FROM {table}
        WHERE {" AND ".join(filters)}
        ORDER BY datetime ASC
    """
    return list(conn.execute(sql, params))


def query_factor_by_date(
    conn: sqlite3.Connection,
    factor_table: str,
    req: HistoryRequest,
) -> dict[date, float]:
    filters: list[str] = [
        "symbol = ?",
        "exchange = ?",
        "interval = ?",
    ]
    params: list[str] = [
        req.symbol,
        req.exchange.value,
        SOURCE_INTERVAL,
    ]

    append_date_filters(filters, params, req)

    sql: str = f"""
        SELECT datetime, adj_factor
        FROM {factor_table}
        WHERE {" AND ".join(filters)}
        ORDER BY datetime ASC
    """

    return {
        parse_local_datetime(row["datetime"]).date(): row["adj_factor"]
        for row in conn.execute(sql, params)
        if row["adj_factor"] is not None and row["adj_factor"] != 0
    }


def create_bar(
    req: HistoryRequest,
    row: sqlite3.Row,
    adj_factor: float,
    denominator_factor: float,
) -> BarData:
    multiplier: float = adj_factor / denominator_factor

    return BarData(
        symbol=req.symbol,
        exchange=req.exchange,
        interval=Interval.DAILY,
        datetime=parse_local_datetime(row["datetime"]),
        open_price=adjust_price(row["open"], multiplier),
        high_price=adjust_price(row["high"], multiplier),
        low_price=adjust_price(row["low"], multiplier),
        close_price=adjust_price(row["close"], multiplier),
        volume=row["vol"] or 0,
        turnover=row["amount"] or 0,
        open_interest=0,
        gateway_name="LOCAL",
    )


def append_date_filters(
    filters: list[str],
    params: list[str],
    req: HistoryRequest,
) -> None:
    start: str | None = format_start_datetime(req.start)
    end: str | None = format_end_datetime(req.end)

    if start:
        filters.append("datetime >= ?")
        params.append(start)

    if end:
        filters.append("datetime <= ?")
        params.append(end)


def format_start_datetime(value: datetime | str | None) -> str | None:
    if not value:
        return None

    dt: datetime = normalize_datetime(value)
    return datetime.combine(dt.date(), time.min).strftime("%Y-%m-%d %H:%M:%S")


def format_end_datetime(value: datetime | str | None) -> str | None:
    if not value:
        return None

    dt: datetime = normalize_datetime(value)
    return datetime.combine(dt.date(), time.max).strftime("%Y-%m-%d %H:%M:%S")


def normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value

    return datetime.fromisoformat(value)


def parse_local_datetime(value: str) -> datetime:
    dt: datetime = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CHINA_TZ)

    return dt.astimezone(CHINA_TZ)


def adjust_price(value: float | None, multiplier: float) -> float:
    if value is None:
        return 0.0

    return float(round(value * multiplier, 2))


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)

    uri: str = f"file:{quote(str(path), safe='/')}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def query_local_open_dates(
    start_date: date,
    end_date: date,
) -> tuple[date, ...]:
    """从本地行情表反推出观测到的交易日期。"""
    with connect_readonly(MARKET_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        union_sql: str = " UNION ".join(
            f"""
            SELECT date(datetime) AS trade_date
            FROM {table}
            WHERE interval = ?
                AND datetime >= ?
                AND datetime <= ?
            """
            for table in CALENDAR_TABLES
        )
        params: list[str] = []
        for _table in CALENDAR_TABLES:
            params.extend(
                [
                    SOURCE_INTERVAL,
                    datetime.combine(start_date, time.min).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    datetime.combine(end_date, time.max).strftime("%Y-%m-%d %H:%M:%S"),
                ]
            )

        return tuple(
            sorted(
                datetime.fromisoformat(row["trade_date"]).date()
                for row in conn.execute(union_sql, params)
            )
        )
