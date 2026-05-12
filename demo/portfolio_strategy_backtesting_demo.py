from datetime import datetime
from pathlib import Path
import sys
import pandas as pd


# Allow running this demo directly from the source checkout without editable installs.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VNPY_ROOT = PROJECT_ROOT / "vnpy"
PORTFOLIO_STRATEGY_ROOT = PROJECT_ROOT / "vnpy_portfoliostrategy"

for path in (VNPY_ROOT, PORTFOLIO_STRATEGY_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)


from vnpy.trader.constant import Interval
from vnpy.trader.setting import SETTINGS
from vnpy_portfoliostrategy import BacktestingEngine

from vnpy.my_strategies.etf_momentum_rotation_strategy import (
    EtfMomentumRotationStrategy,
)


BACKTEST_START = datetime(2023, 1, 1)
BACKTEST_END = datetime(2025, 5, 12)

CAPITAL = 1_000_000
RATE = 1 / 10000
SLIPPAGE = 0
SIZE = 1
PRICETICK = 0.01

DATABASE_NAME = "sqlite"
DATABASE_DATABASE = "my_data.db"
TRADE_LOG_FILENAME = "trade_log.csv"

VT_SYMBOLS = [
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
]


def build_symbol_setting(value: float) -> dict[str, float]:
    """Create the per-symbol parameter dict expected by BacktestingEngine."""
    return {vt_symbol: value for vt_symbol in VT_SYMBOLS}


def export_trade_log(engine: BacktestingEngine) -> None:
    """Export backtesting trades to a CSV file next to this demo script."""
    columns = [
        "datetime",
        "vt_symbol",
        "direction",
        "offset",
        "price",
        "volume",
        "vt_orderid",
        "vt_tradeid",
    ]
    trades = engine.get_all_trades()
    trade_log = pd.DataFrame(
        [
            {
                "datetime": trade.datetime,
                "vt_symbol": trade.vt_symbol,
                "direction": trade.direction.value if trade.direction else "",
                "offset": trade.offset.value,
                "price": trade.price,
                "volume": trade.volume,
                "vt_orderid": trade.vt_orderid,
                "vt_tradeid": trade.vt_tradeid,
            }
            for trade in trades
        ],
        columns=columns,
    )

    output_path = Path(__file__).with_name(TRADE_LOG_FILENAME)
    trade_log.to_csv(output_path, index=False)
    print(f"Trade log exported: {output_path}")


def main() -> None:
    """Run ETF momentum rotation portfolio backtesting demo."""
    SETTINGS["database.name"] = DATABASE_NAME
    SETTINGS["database.database"] = DATABASE_DATABASE

    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbols=VT_SYMBOLS,
        interval=Interval.DAILY,
        start=BACKTEST_START,
        end=BACKTEST_END,
        rates=build_symbol_setting(RATE),
        slippages=build_symbol_setting(SLIPPAGE),
        sizes=build_symbol_setting(SIZE),
        priceticks=build_symbol_setting(PRICETICK),
        capital=CAPITAL,
    )

    setting = {
        "initial_capital": CAPITAL,
    }
    engine.add_strategy(EtfMomentumRotationStrategy, setting)

    engine.load_data()
    engine.run_backtesting()
    engine.calculate_result()
    engine.calculate_statistics()

    export_trade_log(engine)
    engine.show_chart()


if __name__ == "__main__":
    main()
