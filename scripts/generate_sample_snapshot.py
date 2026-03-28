"""Generate frozen PIT sample snapshots for MVP0 development and testing.

Creates a Parquet file with ~20 sample stocks covering:
- Normal stocks (ok state)
- Stocks with missing non-critical data (degraded state)
- ST / low-liquidity / excluded stocks (blocked state)

Run: python scripts/generate_sample_snapshot.py
"""

from pathlib import Path

import polars as pl

# fmt: off
SAMPLE_DATA = [
    # --- Normal stocks (ok) ---
    {
        "ticker": "600519", "name": "贵州茅台", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "食品饮料", "industry_l2": "白酒",
        "listed_trading_days": 5000, "adv20_amount_cny": 5_000_000_000.0,
        "total_equity": 120_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.321, "gross_margin_stability_12q": 0.92, "asset_turnover_delta": 0.03,
        "cfo_to_net_profit_ttm": 1.15, "fcf_ttm_margin": 0.283, "net_debt_to_ebitda": -0.5,
        "receivable_inventory_anomaly": 0.12,
        "revenue_yoy_acceleration": 0.08, "profit_yoy_acceleration": 0.12,
        "operating_margin_delta": 0.02, "industry_regime_strength": 0.75,
        "pe_pctile_in_industry": 0.35, "pb_pctile_in_industry": 0.40, "ev_ebitda_pctile_in_industry": 0.38,
        "rs_60d": 0.72, "ma_structure": 0.80, "turnover_support": 0.65,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": True, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "000858", "name": "五粮液", "exchange": "SZSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "食品饮料", "industry_l2": "白酒",
        "listed_trading_days": 4500, "adv20_amount_cny": 3_000_000_000.0,
        "total_equity": 80_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.265, "gross_margin_stability_12q": 0.88, "asset_turnover_delta": 0.02,
        "cfo_to_net_profit_ttm": 1.05, "fcf_ttm_margin": 0.22, "net_debt_to_ebitda": -0.3,
        "receivable_inventory_anomaly": 0.15,
        "revenue_yoy_acceleration": 0.06, "profit_yoy_acceleration": 0.09,
        "operating_margin_delta": 0.015, "industry_regime_strength": 0.70,
        "pe_pctile_in_industry": 0.28, "pb_pctile_in_industry": 0.35, "ev_ebitda_pctile_in_industry": 0.30,
        "rs_60d": 0.65, "ma_structure": 0.75, "turnover_support": 0.60,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "601318", "name": "中国平安", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "非银金融", "industry_l2": "保险",
        "listed_trading_days": 4200, "adv20_amount_cny": 4_000_000_000.0,
        "total_equity": 600_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.145, "gross_margin_stability_12q": 0.70, "asset_turnover_delta": 0.01,
        "cfo_to_net_profit_ttm": 0.90, "fcf_ttm_margin": 0.15, "net_debt_to_ebitda": 1.2,
        "receivable_inventory_anomaly": 0.08,
        "revenue_yoy_acceleration": 0.04, "profit_yoy_acceleration": 0.06,
        "operating_margin_delta": 0.01, "industry_regime_strength": 0.55,
        "pe_pctile_in_industry": 0.22, "pb_pctile_in_industry": 0.18, "ev_ebitda_pctile_in_industry": 0.20,
        "rs_60d": 0.58, "ma_structure": 0.62, "turnover_support": 0.70,
        "risk_goodwill_high": True, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "300750", "name": "宁德时代", "exchange": "SZSE", "board": "chinext",
        "security_type": "common_stock", "industry_l1": "电力设备", "industry_l2": "电池",
        "listed_trading_days": 1800, "adv20_amount_cny": 8_000_000_000.0,
        "total_equity": 200_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.22, "gross_margin_stability_12q": 0.78, "asset_turnover_delta": 0.05,
        "cfo_to_net_profit_ttm": 1.30, "fcf_ttm_margin": 0.18, "net_debt_to_ebitda": 0.8,
        "receivable_inventory_anomaly": 0.25,
        "revenue_yoy_acceleration": 0.15, "profit_yoy_acceleration": 0.20,
        "operating_margin_delta": 0.03, "industry_regime_strength": 0.85,
        "pe_pctile_in_industry": 0.55, "pb_pctile_in_industry": 0.60, "ev_ebitda_pctile_in_industry": 0.50,
        "rs_60d": 0.80, "ma_structure": 0.85, "turnover_support": 0.75,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "600036", "name": "招商银行", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "银行", "industry_l2": "股份制银行",
        "listed_trading_days": 5500, "adv20_amount_cny": 2_500_000_000.0,
        "total_equity": 400_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.168, "gross_margin_stability_12q": 0.85, "asset_turnover_delta": 0.01,
        "cfo_to_net_profit_ttm": 0.95, "fcf_ttm_margin": 0.12, "net_debt_to_ebitda": 2.0,
        "receivable_inventory_anomaly": 0.05,
        "revenue_yoy_acceleration": 0.03, "profit_yoy_acceleration": 0.05,
        "operating_margin_delta": 0.005, "industry_regime_strength": 0.50,
        "pe_pctile_in_industry": 0.30, "pb_pctile_in_industry": 0.25, "ev_ebitda_pctile_in_industry": 0.28,
        "rs_60d": 0.55, "ma_structure": 0.58, "turnover_support": 0.62,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "000333", "name": "美的集团", "exchange": "SZSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "家用电器", "industry_l2": "白色家电",
        "listed_trading_days": 3000, "adv20_amount_cny": 3_500_000_000.0,
        "total_equity": 180_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.25, "gross_margin_stability_12q": 0.82, "asset_turnover_delta": 0.04,
        "cfo_to_net_profit_ttm": 1.10, "fcf_ttm_margin": 0.20, "net_debt_to_ebitda": 0.5,
        "receivable_inventory_anomaly": 0.18,
        "revenue_yoy_acceleration": 0.10, "profit_yoy_acceleration": 0.14,
        "operating_margin_delta": 0.025, "industry_regime_strength": 0.65,
        "pe_pctile_in_industry": 0.32, "pb_pctile_in_industry": 0.38, "ev_ebitda_pctile_in_industry": 0.35,
        "rs_60d": 0.68, "ma_structure": 0.72, "turnover_support": 0.68,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "002594", "name": "比亚迪", "exchange": "SZSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "汽车", "industry_l2": "整车",
        "listed_trading_days": 3500, "adv20_amount_cny": 6_000_000_000.0,
        "total_equity": 350_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.19, "gross_margin_stability_12q": 0.75, "asset_turnover_delta": 0.06,
        "cfo_to_net_profit_ttm": 1.25, "fcf_ttm_margin": 0.16, "net_debt_to_ebitda": 1.5,
        "receivable_inventory_anomaly": 0.22,
        "revenue_yoy_acceleration": 0.25, "profit_yoy_acceleration": 0.30,
        "operating_margin_delta": 0.04, "industry_regime_strength": 0.90,
        "pe_pctile_in_industry": 0.65, "pb_pctile_in_industry": 0.70, "ev_ebitda_pctile_in_industry": 0.60,
        "rs_60d": 0.85, "ma_structure": 0.88, "turnover_support": 0.80,
        "risk_goodwill_high": False, "risk_equity_pledge_high": True,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "601888", "name": "中国中免", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "社会服务", "industry_l2": "旅游",
        "listed_trading_days": 2800, "adv20_amount_cny": 2_000_000_000.0,
        "total_equity": 90_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.18, "gross_margin_stability_12q": 0.68, "asset_turnover_delta": -0.02,
        "cfo_to_net_profit_ttm": 0.85, "fcf_ttm_margin": 0.10, "net_debt_to_ebitda": 0.3,
        "receivable_inventory_anomaly": 0.30,
        "revenue_yoy_acceleration": -0.05, "profit_yoy_acceleration": -0.08,
        "operating_margin_delta": -0.03, "industry_regime_strength": 0.35,
        "pe_pctile_in_industry": 0.70, "pb_pctile_in_industry": 0.65, "ev_ebitda_pctile_in_industry": 0.68,
        "rs_60d": 0.35, "ma_structure": 0.30, "turnover_support": 0.45,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": True, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "002714", "name": "牧原股份", "exchange": "SZSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "农林牧渔", "industry_l2": "畜禽养殖",
        "listed_trading_days": 2500, "adv20_amount_cny": 2_800_000_000.0,
        "total_equity": 150_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.28, "gross_margin_stability_12q": 0.55, "asset_turnover_delta": 0.08,
        "cfo_to_net_profit_ttm": 0.70, "fcf_ttm_margin": 0.05, "net_debt_to_ebitda": 3.5,
        "receivable_inventory_anomaly": 0.10,
        "revenue_yoy_acceleration": 0.35, "profit_yoy_acceleration": 0.50,
        "operating_margin_delta": 0.08, "industry_regime_strength": 0.80,
        "pe_pctile_in_industry": 0.25, "pb_pctile_in_industry": 0.30, "ev_ebitda_pctile_in_industry": 0.22,
        "rs_60d": 0.78, "ma_structure": 0.82, "turnover_support": 0.72,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "603288", "name": "海天味业", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "食品饮料", "industry_l2": "调味品",
        "listed_trading_days": 2200, "adv20_amount_cny": 1_500_000_000.0,
        "total_equity": 60_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.23, "gross_margin_stability_12q": 0.90, "asset_turnover_delta": 0.01,
        "cfo_to_net_profit_ttm": 1.20, "fcf_ttm_margin": 0.25, "net_debt_to_ebitda": -1.0,
        "receivable_inventory_anomaly": 0.08,
        "revenue_yoy_acceleration": 0.02, "profit_yoy_acceleration": 0.03,
        "operating_margin_delta": 0.005, "industry_regime_strength": 0.45,
        "pe_pctile_in_industry": 0.75, "pb_pctile_in_industry": 0.80, "ev_ebitda_pctile_in_industry": 0.72,
        "rs_60d": 0.40, "ma_structure": 0.42, "turnover_support": 0.50,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },

    # --- Degraded stocks (missing non-critical features) ---
    {
        "ticker": "600887", "name": "伊利股份", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "食品饮料", "industry_l2": "乳制品",
        "listed_trading_days": 4000, "adv20_amount_cny": 2_200_000_000.0,
        "total_equity": 70_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.20, "gross_margin_stability_12q": 0.80, "asset_turnover_delta": 0.02,
        "cfo_to_net_profit_ttm": 1.0, "fcf_ttm_margin": 0.18, "net_debt_to_ebitda": 0.2,
        "receivable_inventory_anomaly": 0.15,
        "revenue_yoy_acceleration": 0.05, "profit_yoy_acceleration": 0.07,
        "operating_margin_delta": 0.01, "industry_regime_strength": 0.60,
        # Missing valuation metrics → non-critical → degraded
        "pe_pctile_in_industry": None, "pb_pctile_in_industry": None, "ev_ebitda_pctile_in_industry": None,
        "rs_60d": 0.55, "ma_structure": 0.60, "turnover_support": 0.58,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "002415", "name": "海康威视", "exchange": "SZSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "电子", "industry_l2": "安防",
        "listed_trading_days": 3800, "adv20_amount_cny": 3_200_000_000.0,
        "total_equity": 250_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.21, "gross_margin_stability_12q": 0.85, "asset_turnover_delta": 0.03,
        "cfo_to_net_profit_ttm": 0.92, "fcf_ttm_margin": 0.14, "net_debt_to_ebitda": 0.6,
        "receivable_inventory_anomaly": 0.28,
        "revenue_yoy_acceleration": 0.07, "profit_yoy_acceleration": 0.10,
        "operating_margin_delta": 0.02, "industry_regime_strength": 0.55,
        "pe_pctile_in_industry": 0.40, "pb_pctile_in_industry": 0.45, "ev_ebitda_pctile_in_industry": 0.42,
        # Missing market confirmation metrics
        "rs_60d": None, "ma_structure": None, "turnover_support": None,
        "risk_goodwill_high": True, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },

    # --- Blocked: Missing critical data ---
    {
        "ticker": "600900", "name": "长江电力", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "公用事业", "industry_l2": "水电",
        "listed_trading_days": 5200, "adv20_amount_cny": 1_800_000_000.0,
        "total_equity": 300_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        # Missing critical metrics: roe_ttm and cfo_to_net_profit_ttm
        "roe_ttm": None,
        "gross_margin_stability_12q": 0.95, "asset_turnover_delta": 0.00,
        "cfo_to_net_profit_ttm": None,
        "fcf_ttm_margin": 0.30, "net_debt_to_ebitda": 2.5,
        "receivable_inventory_anomaly": 0.03,
        "revenue_yoy_acceleration": 0.01, "profit_yoy_acceleration": 0.02,
        "operating_margin_delta": 0.00, "industry_regime_strength": 0.40,
        "pe_pctile_in_industry": 0.50, "pb_pctile_in_industry": 0.55, "ev_ebitda_pctile_in_industry": 0.48,
        "rs_60d": 0.50, "ma_structure": 0.48, "turnover_support": 0.55,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },

    # --- Blocked: ST stock ---
    {
        "ticker": "600978", "name": "*ST宜生", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "医药生物", "industry_l2": "中药",
        "listed_trading_days": 3000, "adv20_amount_cny": 50_000_000.0,
        "total_equity": 2_000_000_000.0, "has_latest_financials": False,
        "has_standard_unqualified_audit": False, "is_st": True, "is_delisting": False, "is_suspended": False,
        "roe_ttm": -0.15, "gross_margin_stability_12q": 0.30, "asset_turnover_delta": -0.05,
        "cfo_to_net_profit_ttm": -0.50, "fcf_ttm_margin": -0.10, "net_debt_to_ebitda": 8.0,
        "receivable_inventory_anomaly": 0.80,
        "revenue_yoy_acceleration": -0.30, "profit_yoy_acceleration": -0.50,
        "operating_margin_delta": -0.10, "industry_regime_strength": 0.10,
        "pe_pctile_in_industry": 0.95, "pb_pctile_in_industry": 0.90, "ev_ebitda_pctile_in_industry": 0.92,
        "rs_60d": 0.10, "ma_structure": 0.08, "turnover_support": 0.15,
        "risk_goodwill_high": True, "risk_equity_pledge_high": True,
        "risk_regulatory_probe": True, "risk_major_reduction": False, "risk_material_negative_announcement": True,
    },

    # --- Blocked: Low liquidity ---
    {
        "ticker": "600985", "name": "某低流动性股", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "机械设备", "industry_l2": "仪器仪表",
        "listed_trading_days": 1500, "adv20_amount_cny": 20_000_000.0,  # Below threshold
        "total_equity": 5_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.08, "gross_margin_stability_12q": 0.60, "asset_turnover_delta": 0.01,
        "cfo_to_net_profit_ttm": 0.80, "fcf_ttm_margin": 0.06, "net_debt_to_ebitda": 1.0,
        "receivable_inventory_anomaly": 0.20,
        "revenue_yoy_acceleration": 0.02, "profit_yoy_acceleration": 0.03,
        "operating_margin_delta": 0.005, "industry_regime_strength": 0.30,
        "pe_pctile_in_industry": 0.60, "pb_pctile_in_industry": 0.55, "ev_ebitda_pctile_in_industry": 0.58,
        "rs_60d": 0.25, "ma_structure": 0.20, "turnover_support": 0.18,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },

    # --- Blocked: Not listed long enough ---
    {
        "ticker": "301505", "name": "某次新股", "exchange": "SZSE", "board": "chinext",
        "security_type": "common_stock", "industry_l1": "计算机", "industry_l2": "软件",
        "listed_trading_days": 30,  # Below 60-day threshold
        "adv20_amount_cny": 500_000_000.0,
        "total_equity": 10_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.15, "gross_margin_stability_12q": 0.70, "asset_turnover_delta": 0.05,
        "cfo_to_net_profit_ttm": 0.90, "fcf_ttm_margin": 0.12, "net_debt_to_ebitda": 0.3,
        "receivable_inventory_anomaly": 0.10,
        "revenue_yoy_acceleration": 0.20, "profit_yoy_acceleration": 0.25,
        "operating_margin_delta": 0.03, "industry_regime_strength": 0.70,
        "pe_pctile_in_industry": 0.80, "pb_pctile_in_industry": 0.85, "ev_ebitda_pctile_in_industry": 0.78,
        "rs_60d": 0.90, "ma_structure": 0.92, "turnover_support": 0.85,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },

    # --- Blocked: ETF (not common_stock) ---
    {
        "ticker": "510050", "name": "上证50ETF", "exchange": "SSE", "board": "main",
        "security_type": "etf", "industry_l1": "基金", "industry_l2": None,
        "listed_trading_days": 4000, "adv20_amount_cny": 10_000_000_000.0,
        "total_equity": 0.0, "has_latest_financials": False,
        "has_standard_unqualified_audit": False, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": None, "gross_margin_stability_12q": None, "asset_turnover_delta": None,
        "cfo_to_net_profit_ttm": None, "fcf_ttm_margin": None, "net_debt_to_ebitda": None,
        "receivable_inventory_anomaly": None,
        "revenue_yoy_acceleration": None, "profit_yoy_acceleration": None,
        "operating_margin_delta": None, "industry_regime_strength": None,
        "pe_pctile_in_industry": None, "pb_pctile_in_industry": None, "ev_ebitda_pctile_in_industry": None,
        "rs_60d": None, "ma_structure": None, "turnover_support": None,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },

    # --- More normal stocks for cross-sectional ranking ---
    {
        "ticker": "000651", "name": "格力电器", "exchange": "SZSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "家用电器", "industry_l2": "白色家电",
        "listed_trading_days": 5000, "adv20_amount_cny": 2_500_000_000.0,
        "total_equity": 100_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.24, "gross_margin_stability_12q": 0.83, "asset_turnover_delta": 0.02,
        "cfo_to_net_profit_ttm": 1.08, "fcf_ttm_margin": 0.19, "net_debt_to_ebitda": 0.4,
        "receivable_inventory_anomaly": 0.20,
        "revenue_yoy_acceleration": 0.08, "profit_yoy_acceleration": 0.10,
        "operating_margin_delta": 0.02, "industry_regime_strength": 0.60,
        "pe_pctile_in_industry": 0.25, "pb_pctile_in_industry": 0.30, "ev_ebitda_pctile_in_industry": 0.28,
        "rs_60d": 0.62, "ma_structure": 0.65, "turnover_support": 0.60,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": True, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "600276", "name": "恒瑞医药", "exchange": "SSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "医药生物", "industry_l2": "化学制药",
        "listed_trading_days": 4800, "adv20_amount_cny": 3_000_000_000.0,
        "total_equity": 120_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.16, "gross_margin_stability_12q": 0.88, "asset_turnover_delta": 0.01,
        "cfo_to_net_profit_ttm": 1.10, "fcf_ttm_margin": 0.22, "net_debt_to_ebitda": -0.8,
        "receivable_inventory_anomaly": 0.12,
        "revenue_yoy_acceleration": 0.12, "profit_yoy_acceleration": 0.15,
        "operating_margin_delta": 0.03, "industry_regime_strength": 0.72,
        "pe_pctile_in_industry": 0.70, "pb_pctile_in_industry": 0.75, "ev_ebitda_pctile_in_industry": 0.68,
        "rs_60d": 0.60, "ma_structure": 0.65, "turnover_support": 0.58,
        "risk_goodwill_high": False, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
    {
        "ticker": "002352", "name": "顺丰控股", "exchange": "SZSE", "board": "main",
        "security_type": "common_stock", "industry_l1": "交通运输", "industry_l2": "物流",
        "listed_trading_days": 2000, "adv20_amount_cny": 1_500_000_000.0,
        "total_equity": 160_000_000_000.0, "has_latest_financials": True,
        "has_standard_unqualified_audit": True, "is_st": False, "is_delisting": False, "is_suspended": False,
        "roe_ttm": 0.10, "gross_margin_stability_12q": 0.72, "asset_turnover_delta": 0.03,
        "cfo_to_net_profit_ttm": 1.35, "fcf_ttm_margin": 0.08, "net_debt_to_ebitda": 1.8,
        "receivable_inventory_anomaly": 0.15,
        "revenue_yoy_acceleration": 0.06, "profit_yoy_acceleration": 0.08,
        "operating_margin_delta": 0.01, "industry_regime_strength": 0.50,
        "pe_pctile_in_industry": 0.45, "pb_pctile_in_industry": 0.50, "ev_ebitda_pctile_in_industry": 0.42,
        "rs_60d": 0.48, "ma_structure": 0.50, "turnover_support": 0.55,
        "risk_goodwill_high": True, "risk_equity_pledge_high": False,
        "risk_regulatory_probe": False, "risk_major_reduction": False, "risk_material_negative_announcement": False,
    },
]
# fmt: on


def main() -> None:
    output_dir = Path("data/sample_snapshots")
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pl.DataFrame(SAMPLE_DATA)
    output_path = output_dir / "snapshot_20260324.parquet"
    df.write_parquet(output_path)

    print(f"✅ Generated {df.height} sample rows → {output_path}")
    print(f"   Columns: {df.columns}")

    # Also generate CSV for easier inspection
    csv_path = output_dir / "snapshot_20260324.csv"
    df.write_csv(csv_path)
    print(f"   CSV copy → {csv_path}")


if __name__ == "__main__":
    main()
