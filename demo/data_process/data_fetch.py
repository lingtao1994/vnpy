from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from vnpy.trader.datafeed import get_datafeed
from vnpy.trader.database import get_database
from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData, HistoryRequest
from vnpy.trader.utility import extract_vt_symbol
from vnpy.trader.setting import SETTINGS
from vnpy_localdata.localdata_datafeed import MARKET_DB_PATH

if TYPE_CHECKING or __package__:
    from .data_quality import (
        BLOCK_SAVE,
        CALENDAR_ERROR,
        MISSING_TRADING_DAY,
        PRICE_JUMP,
        QualityReport,
        create_calendar_error_report,
        get_bar_date_range,
        validate_daily_bar_quality,
        write_quality_reports,
    )
else:
    from data_quality import (
        BLOCK_SAVE,
        CALENDAR_ERROR,
        MISSING_TRADING_DAY,
        PRICE_JUMP,
        QualityReport,
        create_calendar_error_report,
        get_bar_date_range,
        validate_daily_bar_quality,
        write_quality_reports,
    )


DEFAULT_VT_SYMBOLS: tuple[str, ...] = (
    "510300.SSE",
    "510500.SSE",
    "512100.SSE",
    "159915.SZSE",
    "588000.SSE",
    "512880.SSE",
    "510150.SSE",
    "512010.SSE",
    "512480.SSE",
    "515700.SSE",
    "512660.SSE",
    "512800.SSE",
    "510880.SSE",
    "511010.SSE",
    "511880.SSE",
)

# 要下载数据的起止时间
# DEFAULT_START = datetime(2025, 1, 1)
# DEFAULT_END = datetime(2025, 3, 30)
DEFAULT_START: datetime | str | None = None
DEFAULT_END: datetime | str | None = None
DEFAULT_HISTORY_START: datetime = datetime(1900, 1, 1)
DEFAULT_REPORT_PATH: Path = Path.cwd().joinpath("data_quality_report.csv")

DateBoundary = datetime | str | None
CalendarFetcher = Callable[[date, date], Sequence[date]]
Output = Callable[[str], None]


class BarHistoryDatafeed(Protocol):
    def query_bar_history(self, req: HistoryRequest) -> list[BarData]:
        pass


class BarDatabase(Protocol):
    def save_bar_data(self, bars: list[BarData]) -> bool:
        pass


def configure_services() -> None:
    SETTINGS["datafeed.name"] = "localdata"
    SETTINGS["database.name"] = "sqlite"
    SETTINGS["database.database"] = "my_data.db"
    SETTINGS["database.port"] = 0


def create_calendar_fetcher() -> CalendarFetcher:
    from vnpy_localdata import query_local_open_dates

    def fetcher(start_date: date, end_date: date) -> Sequence[date]:
        return query_local_open_dates(
            start_date=start_date,
            end_date=end_date,
        )

    return fetcher


def run_download(
    vt_symbols: Sequence[str],
    start: DateBoundary,
    end: DateBoundary,
    datafeed: BarHistoryDatafeed,
    database: BarDatabase,
    calendar_fetcher: CalendarFetcher,
    report_path: str | Path,
    output: Output = print,
) -> list[QualityReport]:
    reports: list[QualityReport] = []
    if not validate_target_database(database, output):
        return reports

    request_start: datetime = normalize_start(start)
    request_end: datetime | None = normalize_end(end)

    for vt_symbol in vt_symbols:
        symbol, exchange = extract_vt_symbol(vt_symbol)
        req: HistoryRequest = HistoryRequest(
            symbol=symbol,
            exchange=exchange,
            start=request_start,
            end=request_end,
            interval=Interval.DAILY,
        )

        bars: list[BarData] = datafeed.query_bar_history(req)
        if not bars:
            output(f"下载数据失败：{vt_symbol}")
            continue

        report: QualityReport = create_quality_report(
            vt_symbol=vt_symbol,
            req=req,
            bars=bars,
            calendar_fetcher=calendar_fetcher,
        )
        reports.append(report)
        output_quality_summary(report, output)

        if report.can_save:
            database.save_bar_data(bars)
            output(f"下载数据成功：{vt_symbol}，总数据量：{len(bars)}")
        else:
            output(f"数据质量异常，跳过入库：{vt_symbol}，总数据量：{len(bars)}")

    write_quality_reports(reports, report_path)
    output(f"数据质量报告已写入：{Path(report_path).name}")

    return reports


def normalize_start(value: DateBoundary) -> datetime:
    if not value:
        return DEFAULT_HISTORY_START

    if isinstance(value, str):
        return datetime.fromisoformat(value)

    return value


def normalize_end(value: DateBoundary) -> datetime | None:
    if not value:
        return None

    if isinstance(value, str):
        return datetime.fromisoformat(value)

    return value


def validate_target_database(database: object, output: Output) -> bool:
    target_path: Path | None = get_database_path(database)
    if target_path is None:
        return True

    if paths_equal(target_path, MARKET_DB_PATH):
        output("目标数据库与源库相同，已停止入库")
        return False

    return True


def get_database_path(database: object) -> Path | None:
    inner_database: object | None = getattr(database, "db", None)
    path_value: object | None = None

    if inner_database is not None:
        path_value = getattr(inner_database, "database", None)

    if path_value is None:
        path_value = getattr(database, "database", None)

    if path_value is None:
        return None

    return Path(str(path_value)).expanduser()


def paths_equal(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.expanduser().resolve(strict=False)


def create_quality_report(
    vt_symbol: str,
    req: HistoryRequest,
    bars: Sequence[BarData],
    calendar_fetcher: CalendarFetcher,
) -> QualityReport:
    start_date, end_date = get_bar_date_range(bars)
    if start_date is None or end_date is None:
        return QualityReport(
            vt_symbol=vt_symbol, total_bars=0, start_date=None, end_date=None
        )

    try:
        trading_dates: tuple[date, ...] = tuple(calendar_fetcher(start_date, end_date))
    except Exception as exc:
        return create_calendar_error_report(
            vt_symbol=vt_symbol,
            bars=bars,
            error_type=type(exc).__name__,
        )

    if not trading_dates:
        return create_calendar_error_report(
            vt_symbol=vt_symbol,
            bars=bars,
            error_type="EmptyCalendar",
        )

    return validate_daily_bar_quality(
        vt_symbol=vt_symbol,
        req=req,
        bars=bars,
        trading_dates=trading_dates,
    )


def output_quality_summary(report: QualityReport, output: Output) -> None:
    if not report.issues:
        output(f"数据质量通过：{report.vt_symbol}")
        return

    missing_dates: tuple[str, ...] = tuple(
        issue.trade_date.isoformat()
        for issue in report.issues
        if issue.issue_type == MISSING_TRADING_DAY and issue.trade_date is not None
    )
    price_jumps: tuple[str, ...] = tuple(
        f"{issue.trade_date.isoformat()}({issue.change_pct:.2%})"
        for issue in report.issues
        if issue.issue_type == PRICE_JUMP
        and issue.trade_date is not None
        and issue.change_pct is not None
    )
    calendar_errors: tuple[str, ...] = tuple(
        issue.message for issue in report.issues if issue.issue_type == CALENDAR_ERROR
    )

    if missing_dates:
        output(
            f"交易日缺失告警：{report.vt_symbol}，缺失{len(missing_dates)}天：{format_items(missing_dates)}"
        )

    if price_jumps:
        output(
            f"价格异常：{report.vt_symbol}，异常{len(price_jumps)}处：{format_items(price_jumps)}"
        )

    if calendar_errors:
        output(f"交易日历异常：{report.vt_symbol}，{format_items(calendar_errors)}")

    if any(issue.action == BLOCK_SAVE for issue in report.issues):
        output(f"质量检查结果：{report.vt_symbol} 阻止入库")


def format_items(items: Sequence[str], limit: int = 10) -> str:
    visible_items: Sequence[str] = items[:limit]
    suffix: str = "" if len(items) <= limit else f"... 共{len(items)}项"
    return ", ".join(visible_items) + suffix


def main() -> None:
    configure_services()
    datafeed = get_datafeed()
    database = get_database()
    calendar_fetcher: CalendarFetcher = create_calendar_fetcher()

    run_download(
        vt_symbols=DEFAULT_VT_SYMBOLS,
        start=DEFAULT_START,
        end=DEFAULT_END,
        datafeed=datafeed,
        database=database,
        calendar_fetcher=calendar_fetcher,
        report_path=DEFAULT_REPORT_PATH,
    )


if __name__ == "__main__":
    main()
