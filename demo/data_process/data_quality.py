import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData, HistoryRequest


PRICE_JUMP_THRESHOLD: float = 0.10
MISSING_TRADING_DAY: str = "missing_trading_day"
PRICE_JUMP: str = "price_jump"
CALENDAR_ERROR: str = "calendar_error"
WARN_SAVE: str = "warn_save"
BLOCK_SAVE: str = "block_save"

CSV_FIELDS: tuple[str, ...] = (
    "vt_symbol",
    "issue_type",
    "trade_date",
    "prev_trade_date",
    "prev_close",
    "open_price",
    "change_pct",
    "action",
    "message",
)


@dataclass(frozen=True)
class QualityIssue:
    vt_symbol: str
    issue_type: str
    trade_date: date | None
    prev_trade_date: date | None = None
    prev_close: float | None = None
    open_price: float | None = None
    change_pct: float | None = None
    action: str = WARN_SAVE
    message: str = ""

    def to_csv_row(self) -> dict[str, str]:
        return {
            "vt_symbol": self.vt_symbol,
            "issue_type": self.issue_type,
            "trade_date": format_date(self.trade_date),
            "prev_trade_date": format_date(self.prev_trade_date),
            "prev_close": format_float(self.prev_close),
            "open_price": format_float(self.open_price),
            "change_pct": format_float(self.change_pct),
            "action": self.action,
            "message": self.message,
        }


@dataclass(frozen=True)
class QualityReport:
    vt_symbol: str
    total_bars: int
    start_date: date | None
    end_date: date | None
    issues: tuple[QualityIssue, ...] = ()

    @property
    def can_save(self) -> bool:
        return all(issue.action != BLOCK_SAVE for issue in self.issues)


def format_date(value: date | None) -> str:
    if value is None:
        return ""

    return value.isoformat()


def format_float(value: float | None) -> str:
    if value is None:
        return ""

    return f"{value:.6f}"


def get_bar_date_range(bars: Sequence[BarData]) -> tuple[date | None, date | None]:
    if not bars:
        return None, None

    dates: tuple[date, ...] = tuple(sorted({bar.datetime.date() for bar in bars}))
    return dates[0], dates[-1]


def validate_daily_bar_quality(
    vt_symbol: str,
    req: HistoryRequest,
    bars: Sequence[BarData],
    trading_dates: Iterable[date],
    jump_threshold: float = PRICE_JUMP_THRESHOLD,
) -> QualityReport:
    start_date, end_date = get_bar_date_range(bars)

    if not bars or req.interval != Interval.DAILY:
        return QualityReport(
            vt_symbol=vt_symbol,
            total_bars=len(bars),
            start_date=start_date,
            end_date=end_date,
        )

    bar_by_date: dict[date, BarData] = {
        bar.datetime.date(): bar for bar in sorted(bars, key=lambda bar: bar.datetime)
    }
    bar_dates: tuple[date, ...] = tuple(sorted(bar_by_date))
    expected_dates: tuple[date, ...] = normalize_trading_dates(
        trading_dates=trading_dates,
        start_date=start_date,
        end_date=end_date,
    )

    missing_issues: tuple[QualityIssue, ...] = tuple(
        QualityIssue(
            vt_symbol=vt_symbol,
            issue_type=MISSING_TRADING_DAY,
            trade_date=trade_date,
            action=WARN_SAVE,
            message="trading day has no bar data",
        )
        for trade_date in expected_dates
        if trade_date not in bar_by_date
    )
    price_issues: tuple[QualityIssue, ...] = find_price_jump_issues(
        vt_symbol=vt_symbol,
        bar_by_date=bar_by_date,
        bar_dates=bar_dates,
        expected_dates=expected_dates,
        jump_threshold=jump_threshold,
    )

    return QualityReport(
        vt_symbol=vt_symbol,
        total_bars=len(bars),
        start_date=start_date,
        end_date=end_date,
        issues=missing_issues + price_issues,
    )


def normalize_trading_dates(
    trading_dates: Iterable[date],
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, ...]:
    if start_date is None or end_date is None:
        return ()

    return tuple(
        sorted(
            {
                trade_date
                for trade_date in trading_dates
                if start_date <= trade_date <= end_date
            }
        )
    )


def find_price_jump_issues(
    vt_symbol: str,
    bar_by_date: dict[date, BarData],
    bar_dates: Sequence[date],
    expected_dates: Sequence[date],
    jump_threshold: float,
) -> tuple[QualityIssue, ...]:
    next_expected_by_date: dict[date, date] = {
        current_date: next_date
        for current_date, next_date in zip(expected_dates, expected_dates[1:])
    }

    return tuple(
        issue
        for prev_date, trade_date in zip(bar_dates, bar_dates[1:])
        if next_expected_by_date.get(prev_date) == trade_date
        for issue in create_price_jump_issue(
            vt_symbol=vt_symbol,
            prev_date=prev_date,
            trade_date=trade_date,
            prev_bar=bar_by_date[prev_date],
            bar=bar_by_date[trade_date],
            jump_threshold=jump_threshold,
        )
    )


def create_price_jump_issue(
    vt_symbol: str,
    prev_date: date,
    trade_date: date,
    prev_bar: BarData,
    bar: BarData,
    jump_threshold: float,
) -> tuple[QualityIssue, ...]:
    if prev_bar.close_price == 0:
        return ()

    change_pct: float = bar.open_price / prev_bar.close_price - 1
    if abs(change_pct) <= jump_threshold:
        return ()

    return (
        QualityIssue(
            vt_symbol=vt_symbol,
            issue_type=PRICE_JUMP,
            trade_date=trade_date,
            prev_trade_date=prev_date,
            prev_close=prev_bar.close_price,
            open_price=bar.open_price,
            change_pct=change_pct,
            action=BLOCK_SAVE,
            message="qfq open price jumps more than threshold from previous close",
        ),
    )


def create_calendar_error_report(
    vt_symbol: str,
    bars: Sequence[BarData],
    error_type: str,
) -> QualityReport:
    start_date, end_date = get_bar_date_range(bars)

    return QualityReport(
        vt_symbol=vt_symbol,
        total_bars=len(bars),
        start_date=start_date,
        end_date=end_date,
        issues=(
            QualityIssue(
                vt_symbol=vt_symbol,
                issue_type=CALENDAR_ERROR,
                trade_date=None,
                action=BLOCK_SAVE,
                message=f"trade calendar fetch failed: {error_type}",
            ),
        ),
    )


def query_tushare_open_dates(
    start_date: date,
    end_date: date,
    api: Any | None = None,
    exchange: str = "SSE",
) -> tuple[date, ...]:
    if api is None:
        import tushare as ts

        api = ts.pro_api()

    df = api.trade_cal(
        exchange=exchange,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        is_open="1",
    )
    if df is None or "cal_date" not in df:
        return ()

    return tuple(
        sorted(
            {
                datetime.strptime(str(cal_date), "%Y%m%d").date()
                for cal_date in df["cal_date"].tolist()
            }
        )
    )


def write_quality_reports(
    reports: Sequence[QualityReport],
    report_path: str | Path,
) -> None:
    path: Path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows: tuple[dict[str, str], ...] = tuple(
        issue.to_csv_row() for report in reports for issue in report.issues
    )

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
