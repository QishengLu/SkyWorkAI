import importlib

# Core environments (always available)
from .file_system_environment import FileSystemEnvironment
from .faiss_environment import FaissEnvironment
from .server import ecp

# Optional environments - import gracefully to avoid breaking on missing deps
_optional_environments = [
    ("github_environment", "GitHubEnvironment"),
    ("interday_trading_environment", "InterdayTradingEnvironment"),
    ("intraday_trading_environment", "IntradayTradingEnvironment"),
    ("database_environment", "DatabaseEnvironment"),
    ("operator_browser_environment", "OperatorBrowserEnvironment"),
    ("mobile_environment", "MobileEnvironment"),
    ("anthropic_mobile_environment", "AnthropicMobileEnvironment"),
    ("alpaca_environment", "AlpacaEnvironment"),
    ("binance_environment", "BinanceEnvironment"),
    ("hyperliquid_environment", "OnlineHyperliquidEnvironment"),
    ("hyperliquid_environment", "OfflineHyperliquidEnvironment"),
    ("quickbacktest_environment", "QuickBacktestEnvironment"),
    ("signal_research_environment", "SignalResearchEnvironment"),
]

_loaded = {}
for _mod_name, _cls_name in _optional_environments:
    try:
        _mod = importlib.import_module(f".{_mod_name}", package=__name__)
        _cls = getattr(_mod, _cls_name)
        _loaded[_cls_name] = _cls
        globals()[_cls_name] = _cls
    except (ImportError, AttributeError):
        pass

__all__ = [
    "FileSystemEnvironment",
    "FaissEnvironment",
    "ecp",
] + list(_loaded.keys())
