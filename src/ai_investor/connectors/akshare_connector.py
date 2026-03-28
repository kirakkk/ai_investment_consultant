"""AKShare connector — pull live A-share data from 东方财富 via AKShare.

MVP1 simplifications (see AGENT.md §十二 S-01~S-07):
- gross_margin_stability uses 4Q window (not 12Q)
- Industry classification from 东财行业板块 (not 申万)
- Risk flags use simple thresholds
- risk_material_negative_announcement always False
- Data freshness SLA skipped
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache

import polars as pl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Throttle helper — AKShare free tier has implicit rate limits
# ---------------------------------------------------------------------------

_THROTTLE_SECONDS = 1.0
_MAX_RETRIES = 6


def _throttled_call(func, *args, **kwargs):
    """Call an AKShare function with rate-limit throttling and retry.

    Retries up to _MAX_RETRIES times with generous backoff on
    connection errors (common during full-market scans).
    """
    import random

    # Add random jitter to prevent all threads hitting server at once
    time.sleep(_THROTTLE_SECONDS + random.uniform(0.1, 1.5))

    for attempt in range(_MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except (ConnectionError, OSError, Exception) as e:
            err_str = str(e).lower()
            # THS drops connections via SSLEOFError or Max retries exceeded
            is_retryable = any(k in err_str for k in [
                "connection", "timeout", "remote", "reset",
                "aborted", "disconnected", "timed out",
                "ssl", "eof", "max retries", "read",
            ])
            if is_retryable and attempt < _MAX_RETRIES - 1:
                wait = 4.0 * (attempt + 1) + random.uniform(2.0, 5.0)
                logger.warning(f"  Retry {attempt+1}/{_MAX_RETRIES} after {wait:.1f}s: {e}")
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Board / security_type inference from ticker code
# ---------------------------------------------------------------------------

def _infer_board(ticker: str) -> str:
    """Infer board from ticker prefix convention."""
    if ticker.startswith("68"):
        return "star"
    if ticker.startswith("30"):
        return "chinext"
    if ticker.startswith("60") or ticker.startswith("00"):
        return "main"
    return "other"


def _infer_exchange(ticker: str) -> str:
    """Infer exchange from ticker prefix."""
    if ticker.startswith("6") or ticker.startswith("9"):
        return "SSE"
    return "SZSE"


class AKShareConnector:
    """AKShare implementation of the DataConnector protocol."""

    def __init__(
        self,
        throttle: float = _THROTTLE_SECONDS,
        disable_proxy: bool = False,
    ) -> None:
        global _THROTTLE_SECONDS
        _THROTTLE_SECONDS = throttle

        if disable_proxy:
            self._bypass_system_proxy()

    # ------------------------------------------------------------------
    # 1. Stock list
    # ------------------------------------------------------------------

    def fetch_stock_list(self) -> pl.DataFrame:
        """Fetch full A-share stock list.

        Primary: 东方财富 spot_em (has PE/PB for valuation percentiles).
        Fallback: stock_info_a_code_name (lighter, different backend).
        """
        import akshare as ak

        logger.info("Fetching full A-share stock list...")

        # Try primary source (eastmoney spot — includes PE/PB)
        try:
            df_pd = _throttled_call(ak.stock_zh_a_spot_em)
            df = pl.from_pandas(df_pd)

            select_exprs = [
                pl.col("代码").alias("ticker"),
                pl.col("名称").alias("name"),
            ]
            if "市盈率-动态" in df.columns:
                select_exprs.append(pl.col("市盈率-动态").cast(pl.Float64, strict=False).alias("pe_ttm_raw"))
            if "市净率" in df.columns:
                select_exprs.append(pl.col("市净率").cast(pl.Float64, strict=False).alias("pb_raw"))
            if "总市值" in df.columns:
                select_exprs.append(pl.col("总市值").cast(pl.Float64, strict=False).alias("market_cap"))

            result = df.select(select_exprs).with_columns([
                pl.col("ticker").map_elements(_infer_exchange, return_dtype=pl.Utf8).alias("exchange"),
                pl.col("ticker").map_elements(_infer_board, return_dtype=pl.Utf8).alias("board"),
                pl.lit("common_stock").alias("security_type"),
            ])
            logger.info(f"  Stock list (eastmoney): {result.height} tickers")
            return result

        except Exception as e:
            logger.warning(f"  Primary stock list failed: {e}")
            logger.info("  Falling back to stock_info_a_code_name...")

        # Fallback source (different backend, no PE/PB)
        df_pd = _throttled_call(ak.stock_info_a_code_name)
        df = pl.from_pandas(df_pd)

        result = df.select([
            pl.col("code").alias("ticker"),
            pl.col("name"),
        ]).with_columns([
            pl.col("ticker").map_elements(_infer_exchange, return_dtype=pl.Utf8).alias("exchange"),
            pl.col("ticker").map_elements(_infer_board, return_dtype=pl.Utf8).alias("board"),
            pl.lit("common_stock").alias("security_type"),
        ])
        logger.info(f"  Stock list (fallback): {result.height} tickers (no PE/PB)")
        return result

    # ------------------------------------------------------------------
    # 2. Financials (via 同花顺 financial abstract — more stable than Sina)
    # ------------------------------------------------------------------

    def fetch_financials(self, tickers: list[str]) -> pl.DataFrame:
        """Fetch key financial indicators via stock_financial_abstract_ths.

        This API is backed by 同花顺 (10jqka) and is significantly more
        reliable than the Sina-backed stock_financial_analysis_indicator
        which suffers ~34% failure rate (D-06).
        Uses ThreadPoolExecutor to accelerate full-universe fetching.
        """
        import akshare as ak
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from tqdm import tqdm

        logger.info(f"Fetching financials for {len(tickers)} tickers (THS multi-threaded)...")
        records: list[dict] = []

        def worker(ticker: str) -> dict:
            try:
                df_fin = _throttled_call(
                    ak.stock_financial_abstract_ths,
                    symbol=ticker,
                    indicator="按报告期",
                )
                if df_fin is None or df_fin.empty:
                    return self._empty_financials(ticker)
                return self._parse_financials_ths(ticker, df_fin)
            except Exception as e:
                logger.warning(f"  {ticker}: financials fetch failed: {e}")
                return self._empty_financials(ticker)

        # Batch progress tracking is better for CI/CD
        future_map = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            for ticker in tickers:
                future_map[executor.submit(worker, ticker)] = ticker
            
            # Using simple tqdm iteration
            for future in tqdm(as_completed(future_map), total=len(tickers), desc="Financials"):
                records.append(future.result())

        return pl.DataFrame(records)

    @staticmethod
    def _parse_ths_value(val: object) -> float | None:
        """Parse THS string-formatted financial values.

        Handles formats like: '74.93%', '-10.26%', '2.17亿', '318.53亿',
        '7.28', 'False', etc.
        """
        if val is None or val is False or str(val).strip() == "False":
            return None
        s = str(val).strip()
        if not s or s == "--" or s == "nan":
            return None

        # Remove % suffix and convert
        if s.endswith("%"):
            try:
                return float(s[:-1])
            except ValueError:
                return None

        # Handle 亿 (hundred million) suffix
        if s.endswith("亿"):
            try:
                return float(s[:-1]) * 1e8
            except ValueError:
                return None

        # Handle 万 (ten thousand) suffix
        if s.endswith("万"):
            try:
                return float(s[:-1]) * 1e4
            except ValueError:
                return None

        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    def _parse_financials_ths(self, ticker: str, df_pd) -> dict:
        """Extract financial metrics from THS financial abstract.

        THS columns (string-formatted):
          净资产收益率      → roe_ttm (e.g. '15.37%')
          每股净资产        → total_equity (× shares, or use 净利润/ROE proxy)
          销售毛利率        → gross_margin_stability
          销售净利率        → operating_margin_delta proxy
          资产负债率        → net_debt_to_ebitda proxy
          每股经营现金流    → cfo_to_net_profit_ttm (with 基本每股收益)
          营业总收入同比增长率 → revenue_yoy_acceleration
          净利润同比增长率  → profit_yoy_acceleration
          存货周转率        → receivable_inventory_anomaly
          应收账款周转天数  → receivable_inventory_anomaly
        """
        rec: dict = {"ticker": ticker}

        # Sort by 报告期 descending to get latest first
        df_pd = df_pd.sort_values("报告期", ascending=False).reset_index(drop=True)

        if len(df_pd) == 0:
            return self._empty_financials(ticker)

        latest = df_pd.iloc[0]

        # --- ROE TTM ---
        roe_val = self._parse_ths_value(latest.get("净资产收益率"))
        if roe_val is None:
            roe_val = self._parse_ths_value(latest.get("净资产收益率-摊薄"))
        rec["roe_ttm"] = roe_val / 100.0 if roe_val is not None else None

        # --- Total equity (净资产 ≈ 净利润 / ROE) ---
        net_profit = self._parse_ths_value(latest.get("净利润"))
        eps = self._parse_ths_value(latest.get("每股净资产"))
        if eps is not None and eps > 0 and net_profit is not None and roe_val is not None and roe_val != 0:
            # total_equity = net_profit / (ROE/100)
            rec["total_equity"] = abs(net_profit / (roe_val / 100.0))
        elif eps is not None:
            # fallback: use 每股净资产 as proxy (missing total shares)
            rec["total_equity"] = eps * 1e8 if eps > 0 else None  # rough proxy
        else:
            rec["total_equity"] = None

        # --- CFO / Net Profit TTM (D-02 fallback) ---
        cfo_ps = self._parse_ths_value(latest.get("每股经营现金流"))
        eps_val = self._parse_ths_value(latest.get("基本每股收益"))
        if cfo_ps is not None and eps_val is not None and eps_val != 0:
            rec["cfo_to_net_profit_ttm"] = cfo_ps / eps_val
        else:
            rec["cfo_to_net_profit_ttm"] = -1.0  # D-02 penalty

        # --- FCF TTM margin (proxy: 销售净利率 × CFO/NP ratio) ---
        net_margin = self._parse_ths_value(latest.get("销售净利率"))
        if net_margin is not None and rec["cfo_to_net_profit_ttm"] > 0:
            rec["fcf_ttm_margin"] = (net_margin / 100.0) * rec["cfo_to_net_profit_ttm"] * 0.7
        else:
            rec["fcf_ttm_margin"] = 0.0

        # --- Net debt / EBITDA (D-03 fallback) ---
        debt_ratio = self._parse_ths_value(latest.get("资产负债率"))
        if debt_ratio is not None and rec["total_equity"] is not None:
            total_assets_est = rec["total_equity"] / max(1.0 - debt_ratio / 100.0, 0.01)
            total_debt = total_assets_est * debt_ratio / 100.0
            if net_profit is not None and net_profit > 0:
                ebitda_est = net_profit * 1.3  # rough EBITDA ≈ NP × 1.3
                rec["net_debt_to_ebitda"] = total_debt / ebitda_est if ebitda_est > 0 else 10.0
            else:
                rec["net_debt_to_ebitda"] = 10.0  # D-03 penalty
        else:
            rec["net_debt_to_ebitda"] = 10.0

        # --- Gross margin stability (D-01) ---
        n_rows = min(len(df_pd), 4)
        gm_values = []
        for j in range(n_rows):
            gm = self._parse_ths_value(df_pd.iloc[j].get("销售毛利率"))
            if gm is not None:
                gm_values.append(gm)
        if len(gm_values) >= 2:
            import statistics
            std = statistics.stdev(gm_values)
            rec["gross_margin_stability_12q"] = 1.0 / (1.0 + std) if std > 0 else 1.0
        else:
            rec["gross_margin_stability_12q"] = 0.5

        # --- Asset turnover delta ---
        # THS doesn't have direct 总资产周转率, use 存货周转率 as proxy
        if len(df_pd) >= 2:
            inv_curr = self._parse_ths_value(latest.get("存货周转率"))
            inv_prev = self._parse_ths_value(df_pd.iloc[1].get("存货周转率"))
            if inv_curr is not None and inv_prev is not None:
                rec["asset_turnover_delta"] = inv_curr - inv_prev
            else:
                rec["asset_turnover_delta"] = None
        else:
            rec["asset_turnover_delta"] = None

        # --- Receivable/inventory anomaly ---
        if len(df_pd) >= 2:
            inv_curr_val = self._parse_ths_value(latest.get("存货周转率"))
            inv_prev_val = self._parse_ths_value(df_pd.iloc[1].get("存货周转率"))
            ar_days_curr = self._parse_ths_value(latest.get("应收账款周转天数"))
            ar_days_prev = self._parse_ths_value(df_pd.iloc[1].get("应收账款周转天数"))
            if all(v is not None for v in [inv_curr_val, inv_prev_val, ar_days_curr, ar_days_prev]):
                # Worsening = inventory turnover dropping AND AR days increasing
                inv_delta = (inv_curr_val - inv_prev_val) / max(abs(inv_prev_val), 1e-6)
                ar_delta = (ar_days_curr - ar_days_prev) / max(abs(ar_days_prev), 1e-6)
                anomaly = max(0.0, min(1.0, (-inv_delta + ar_delta) / 2.0))
                rec["receivable_inventory_anomaly"] = anomaly
            else:
                rec["receivable_inventory_anomaly"] = None
        else:
            rec["receivable_inventory_anomaly"] = None

        # --- Growth: revenue YoY acceleration ---
        if len(df_pd) >= 2:
            rev_curr = self._parse_ths_value(latest.get("营业总收入同比增长率"))
            rev_prev = self._parse_ths_value(df_pd.iloc[1].get("营业总收入同比增长率"))
            if rev_curr is not None and rev_prev is not None:
                rec["revenue_yoy_acceleration"] = (rev_curr - rev_prev) / 100.0
            else:
                rec["revenue_yoy_acceleration"] = None
        else:
            rec["revenue_yoy_acceleration"] = None

        # --- Growth: profit YoY acceleration (D-04 winsorize) ---
        if len(df_pd) >= 2:
            np_curr = self._parse_ths_value(latest.get("净利润同比增长率"))
            np_prev = self._parse_ths_value(df_pd.iloc[1].get("净利润同比增长率"))
            if np_curr is not None and np_prev is not None:
                curr_c = max(-500.0, min(500.0, np_curr))
                prev_c = max(-500.0, min(500.0, np_prev))
                rec["profit_yoy_acceleration"] = (curr_c - prev_c) / 100.0
            else:
                rec["profit_yoy_acceleration"] = 0.0
        else:
            rec["profit_yoy_acceleration"] = 0.0

        # --- Growth: operating margin delta (use 销售净利率 as proxy) ---
        if len(df_pd) >= 2:
            npm_curr = self._parse_ths_value(latest.get("销售净利率"))
            npm_prev = self._parse_ths_value(df_pd.iloc[1].get("销售净利率"))
            if npm_curr is not None and npm_prev is not None:
                rec["operating_margin_delta"] = (npm_curr - npm_prev) / 100.0
            else:
                rec["operating_margin_delta"] = None
        else:
            rec["operating_margin_delta"] = None

        # --- Base metrics for valuation (PE/PB) ---
        rec["eps_ttm"] = self._parse_ths_value(latest.get("基本每股收益"))
        rec["bps"] = self._parse_ths_value(latest.get("每股净资产"))

        # --- 3Y Growth Proxies (CAGR via avg YoY over ~12 quarters) ---
        # THS abstract is quarterly. 3 years = 12 quarters.
        lookback = min(len(df_pd), 12)
        rev_yoys = []
        profit_yoys = []
        for i in range(lookback):
            r_val = self._parse_ths_value(df_pd.iloc[i].get("营业总收入同比增长率"))
            if r_val is not None:
                rev_yoys.append(r_val)
            p_val = self._parse_ths_value(df_pd.iloc[i].get("净利润同比增长率"))
            if p_val is not None:
                profit_yoys.append(p_val)
        
        rec["revenue_cagr_3y"] = (sum(rev_yoys) / len(rev_yoys)) / 100.0 if rev_yoys else None
        rec["profit_cagr_3y"] = (sum(profit_yoys) / len(profit_yoys)) / 100.0 if profit_yoys else None

        # Flags
        rec["has_latest_financials"] = True
        rec["has_standard_unqualified_audit"] = True  # Simplified in MVP2

        return rec

    def _empty_financials(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "roe_ttm": None, "gross_margin_stability_12q": None,
            "asset_turnover_delta": None, "cfo_to_net_profit_ttm": None,
            "fcf_ttm_margin": None, "net_debt_to_ebitda": None,
            "receivable_inventory_anomaly": None,
            "revenue_yoy_acceleration": None, "profit_yoy_acceleration": None,
            "operating_margin_delta": None,
            "eps_ttm": None, "bps": None,
            "revenue_cagr_3y": None, "profit_cagr_3y": None,
            "has_latest_financials": False,
            "has_standard_unqualified_audit": False,
            "total_equity": None,
        }

    # ------------------------------------------------------------------
    # 3. Market data (行情 + 技术指标)
    # ------------------------------------------------------------------

    def fetch_market_data(self, tickers: list[str]) -> pl.DataFrame:
        """Fetch historical market data and compute derived metrics."""
        import akshare as ak
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from tqdm import tqdm
        from datetime import datetime, timedelta

        logger.info(f"Fetching market data for {len(tickers)} tickers (multi-threaded)...")
        records: list[dict] = []

        # Get dates for fallback limit (Tencent API is slow if not limited)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=120)
        end_str = end_date.strftime("%Y%m%d")
        start_str = start_date.strftime("%Y%m%d")

        def _get_tx_symbol(ticker: str) -> str:
            if ticker.startswith(("6", "9")):
                return "sh" + ticker
            elif ticker.startswith(("0", "3")):
                return "sz" + ticker
            elif ticker.startswith(("4", "8")):
                return "bj" + ticker
            return "sz" + ticker

        def _worker(ticker: str) -> dict:
            try:
                # Use Tencent API directly to bypass Eastmoney IP blocks
                df_hist = _throttled_call(
                    ak.stock_zh_a_hist_tx,
                    symbol=_get_tx_symbol(ticker),
                    start_date=start_str,
                    end_date=end_str,
                    adjust="qfq",
                )

                if df_hist is None or df_hist.empty:
                    return self._empty_market_data(ticker)

                df = pl.from_pandas(df_hist)
                return self._compute_market_metrics(ticker, df)
            except Exception as e:
                logger.warning(f"  {ticker}: market data fetch failed: {e}")
                return self._empty_market_data(ticker)

        # Execute concurrently
        future_map = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            for ticker in tickers:
                future_map[executor.submit(_worker, ticker)] = ticker
            
            for future in tqdm(as_completed(future_map), total=len(tickers), desc="Market Data"):
                records.append(future.result())

        return pl.DataFrame(records)

    def _compute_market_metrics(self, ticker: str, df: pl.DataFrame) -> dict:
        """Compute market-derived metrics from daily OHLCV history."""
        rec: dict = {"ticker": ticker}

        # Listed trading days
        rec["listed_trading_days"] = df.height

        # ADV20 (20-day average daily volume in CNY)
        # Tencent API uses "amount", Eastmoney uses "成交额"
        amt_col = self._find_col(df, ["成交额", "amount"])
        if amt_col and df.height >= 20:
            rec["adv20_amount_cny"] = df[amt_col].tail(20).mean()
        elif amt_col:
            rec["adv20_amount_cny"] = df[amt_col].mean()
        else:
            rec["adv20_amount_cny"] = None

        # Close for technical indicators
        close_col = self._find_col(df, ["收盘", "close"])
        if close_col and df.height >= 60:
            closes = df[close_col].to_list()

            # RS 60d (relative strength: current price / price 60 days ago - 1)
            current = float(closes[-1])
            past_60 = float(closes[-60])
            rs_60 = (current / past_60 - 1) if past_60 != 0 else 0
            rec["rs_60d"] = max(0, min(1, (rs_60 + 0.5)))  # normalize to ~[0,1]

            # MA structure (multi-MA alignment score)
            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10
            ma20 = sum(closes[-20:]) / 20
            ma60 = sum(closes[-60:]) / 60
            # Score: how many shorter MAs are above longer ones
            alignment = 0
            if ma5 > ma10:
                alignment += 1
            if ma10 > ma20:
                alignment += 1
            if ma20 > ma60:
                alignment += 1
            rec["ma_structure"] = alignment / 3.0

            # Turnover support (volume trend)
            if df.height >= 20 and amt_col:
                vol_recent = df[amt_col].tail(5).mean()
                vol_prior = df[amt_col].tail(20).head(15).mean()
                if vol_prior and vol_prior > 0 and vol_recent is not None:
                    ratio = vol_recent / vol_prior
                    rec["turnover_support"] = max(0, min(1, ratio / 2.0))
                else:
                    rec["turnover_support"] = 0.5
            else:
                rec["turnover_support"] = 0.5
        else:
            rec["rs_60d"] = None
            rec["ma_structure"] = None
            rec["turnover_support"] = None

        # Growth metrics — need periodic financial data, set None for now
        # These will be computed from financial data in snapshot_builder
        rec["revenue_yoy_acceleration"] = None
        rec["profit_yoy_acceleration"] = None
        rec["operating_margin_delta"] = None
        rec["industry_regime_strength"] = None

        # Valuation percentiles — need cross-sectional data, computed in snapshot_builder
        rec["pe_pctile_in_industry"] = None
        rec["pb_pctile_in_industry"] = None
        rec["ev_ebitda_pctile_in_industry"] = None

        return rec

    def _empty_market_data(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "listed_trading_days": 0, "adv20_amount_cny": None,
            "revenue_yoy_acceleration": None, "profit_yoy_acceleration": None,
            "operating_margin_delta": None, "industry_regime_strength": None,
            "pe_pctile_in_industry": None, "pb_pctile_in_industry": None,
            "ev_ebitda_pctile_in_industry": None,
            "rs_60d": None, "ma_structure": None, "turnover_support": None,
        }

    # ------------------------------------------------------------------
    # 4. Valuation (PE/PB)
    # ------------------------------------------------------------------

    def fetch_valuation(self, tickers: list[str]) -> pl.DataFrame:
        """Fetch raw PE and PB using Baidu valuation APIs.

        Uses ThreadPoolExecutor for fast concurrent fetching, as this
        requires one API call per stock.
        """
        import akshare as ak
        import concurrent.futures

        logger.info("Fetching valuation metrics (Baidu)...")

        def _fetch_single(ticker: str) -> dict:
            rec = {"ticker": ticker, "pe_ttm_raw": None, "pb_raw": None}
            try:
                # API doesn't mind calling twice, it's fast enough
                df_pe = _throttled_call(ak.stock_zh_valuation_baidu, symbol=ticker, indicator="市盈率(TTM)", period="近一年")
                if df_pe is not None and not df_pe.empty:
                    rec["pe_ttm_raw"] = float(df_pe.tail(1)["value"].values[0])
            except Exception:
                pass

            try:
                df_pb = _throttled_call(ak.stock_zh_valuation_baidu, symbol=ticker, indicator="市净率", period="近一年")
                if df_pb is not None and not df_pb.empty:
                    rec["pb_raw"] = float(df_pb.tail(1)["value"].values[0])
            except Exception:
                pass

            return rec

        records = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for i, rec in enumerate(executor.map(_fetch_single, tickers)):
                records.append(rec)
                if i > 0 and i % 500 == 0:
                    logger.info(f"  Valuation progress: {i}/{len(tickers)}")

        mapped = sum(1 for r in records if r["pe_ttm_raw"] is not None)
        logger.info(f"  Valuation mapped: {mapped}/{len(tickers)} ({mapped/(len(tickers) or 1)*100:.1f}%)")

        return pl.DataFrame(records)

    # ------------------------------------------------------------------
    # 5. Risk flags
    # ------------------------------------------------------------------

    def fetch_risk_flags(self, tickers: list[str]) -> pl.DataFrame:
        """Fetch risk flags: ST status, suspension, and simple thresholds."""
        import akshare as ak

        logger.info("Fetching risk flags...")

        # Get ST stock list
        try:
            st_df = _throttled_call(ak.stock_zh_a_st_em)
            st_set = set(st_df["代码"].tolist()) if st_df is not None else set()
        except Exception as e:
            logger.warning(f"  ST list fetch failed: {e}")
            st_set = set()

        # Get current quotes to check suspension (volume = 0)
        try:
            spot = _throttled_call(ak.stock_zh_a_spot_em)
            suspended_set = set()
            if spot is not None:
                for _, row in spot.iterrows():
                    if row.get("成交量", 1) == 0:
                        suspended_set.add(str(row["代码"]))
        except Exception:
            suspended_set = set()

        # Get Goodwill (商誉占净资产比例 > 40% high risk)
        gw_dict = {}
        try:
            gw_df = _throttled_call(ak.stock_sy_em)
            if gw_df is not None and not gw_df.empty:
                for _, row in gw_df.iterrows():
                    ticker = str(row["股票代码"]).strip()
                    val = row.get("商誉占净资产比例")
                    if val is not None and str(val).replace(".", "", 1).isdigit():
                        gw_dict[ticker] = float(val) / 100.0
        except Exception as e:
            logger.warning(f"  Goodwill list fetch failed: {e}")

        # Get Pledge ratio (质押比例 > 40% high risk)
        pledge_dict = {}
        try:
            # Requires a date; we can try without date or use latest
            pledge_df = _throttled_call(ak.stock_gpzy_pledge_ratio_em)
            if pledge_df is not None and not pledge_df.empty:
                for _, row in pledge_df.iterrows():
                    ticker = str(row["股票代码"]).strip()
                    val = row.get("质押比例")
                    if val is not None and str(val).replace(".", "", 1).isdigit():
                        pledge_dict[ticker] = float(val) / 100.0
        except Exception as e:
            logger.warning(f"  Pledge list fetch failed: {e}")

        records: list[dict] = []
        for ticker in tickers:
            records.append({
                "ticker": ticker,
                "is_st": ticker in st_set,
                "is_delisting": False,
                "is_suspended": ticker in suspended_set,
                "risk_goodwill_high": gw_dict.get(ticker, 0.0) > 0.40,
                "risk_equity_pledge_high": pledge_dict.get(ticker, 0.0) > 0.40,
                "risk_regulatory_probe": False,
                "risk_major_reduction": False,
                "risk_material_negative_announcement": False,
            })

        return pl.DataFrame(records)

    # ------------------------------------------------------------------
    # 5. Industry classification (申万行业分类, V2 upgrade)
    # ------------------------------------------------------------------

    def fetch_industry(self, tickers: list[str]) -> pl.DataFrame:
        """Fetch 申万一级行业分类 for given tickers.

        Strategy:
          1. Get all 31 SW L1 industry index codes via sw_index_first_info()
          2. For each L1 index, get constituent stocks via index_component_sw()
          3. Build reverse mapping: stock_code → L1 industry name
        """
        import akshare as ak

        logger.info("Fetching industry classification (申万行业分类)...")

        # ── Step 1: Get all L1 industry codes and names ──
        l1_indices: list[tuple[str, str]] = []  # (code, name)
        try:
            l1_df = _throttled_call(ak.sw_index_first_info)
            if l1_df is not None:
                for _, row in l1_df.iterrows():
                    code = str(row["行业代码"]).replace(".SI", "")
                    name = str(row["行业名称"])
                    l1_indices.append((code, name))
                logger.info(f"  SW L1 industries: {len(l1_indices)}")
        except Exception as e:
            logger.warning(f"  SW L1 info fetch failed: {e}")

        # ── Step 2: Get constituents for each L1 index ──
        ticker_to_industry: dict[str, str] = {}  # stock_code → L1 name
        for i, (idx_code, idx_name) in enumerate(l1_indices):
            try:
                cons_df = _throttled_call(ak.index_component_sw, symbol=idx_code)
                if cons_df is not None and len(cons_df) > 0:
                    for stock_code in cons_df["证券代码"].astype(str).tolist():
                        # First-hit wins: a stock is only mapped to its first L1 match
                        if stock_code not in ticker_to_industry:
                            ticker_to_industry[stock_code] = idx_name
                    logger.info(
                        f"  [{i+1}/{len(l1_indices)}] {idx_name}: "
                        f"{len(cons_df)} constituents"
                    )
            except Exception as e:
                logger.warning(f"  [{i+1}/{len(l1_indices)}] {idx_name}: failed ({e})")

        logger.info(f"  Total mapped stocks: {len(ticker_to_industry)}")

        # ── Step 3: Assemble per-ticker records ──
        records = []
        mapped = 0
        for ticker in tickers:
            industry = ticker_to_industry.get(ticker, "未知")
            if industry != "未知":
                mapped += 1
            records.append({
                "ticker": ticker,
                "industry_l1": industry,
                "industry_l2": None,
                "industry_anchor": industry,  # V2: industry_anchor = SW L1
            })

        logger.info(
            f"  Industry mapped: {mapped}/{len(tickers)} "
            f"({mapped/len(tickers)*100:.1f}%)"
        )

        return pl.DataFrame(records)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bypass_system_proxy() -> None:
        """Disable Windows system proxy for AKShare requests.

        On Windows, `requests` reads proxy settings from the system registry
        (IE settings) via `trust_env=True`. This causes Chinese financial
        APIs (eastmoney.com) to fail when a VPN/proxy is active.

        This patches AKShare's internal request function to bypass proxy.
        """
        import os

        # Clear proxy env vars
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                     "all_proxy", "ALL_PROXY"):
            os.environ.pop(key, None)

        # Directly patch AKShare's request_with_retry to disable trust_env
        try:
            from akshare.utils import request as ak_request
            from akshare.utils import func as ak_func
            import requests
            from requests.adapters import HTTPAdapter
            import random
            import time as _time

            def _patched_request_with_retry(
                url, params=None, timeout=15,
                max_retries=3, base_delay=1.0,
                random_delay_range=(0.5, 1.5),
            ):
                last_exception = None
                for attempt in range(max_retries):
                    try:
                        with requests.Session() as session:
                            session.trust_env = False  # KEY FIX
                            adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1)
                            session.mount("http://", adapter)
                            session.mount("https://", adapter)
                            response = session.get(url, params=params, timeout=timeout)
                            response.raise_for_status()
                            return response
                    except (requests.RequestException, ValueError) as e:
                        last_exception = e
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt) + random.uniform(*random_delay_range)
                            _time.sleep(delay)
                raise last_exception

            # Patch in BOTH modules — func.py uses `from ... import` local binding
            ak_request.request_with_retry = _patched_request_with_retry
            ak_func.request_with_retry = _patched_request_with_retry
            logger.info("  Proxy bypass enabled (patched request_with_retry in request + func)")
        except ImportError:
            logger.warning("  Could not patch AKShare request — proxy bypass may not work")

    @staticmethod
    def _find_col(df: pl.DataFrame, candidates: list[str]) -> str | None:
        """Find the first matching column name from candidates."""
        for c in candidates:
            if c in df.columns:
                return c
        return None

    @staticmethod
    def _safe_float(val: object, scale: float = 1.0) -> float | None:
        """Convert a value to float safely."""
        if val is None:
            return None
        try:
            return float(val) * scale
        except (ValueError, TypeError):
            return None
