# Optimization Report: growth_capture_12m_v1 / 1y

## Governance

- **PIT Mode**: `non_strict_90d`
- **Entry Price**: `t1_close`
- **Slices Used**: 1
- **Trials**: 50
- **Pareto Solutions**: 9

> [!WARNING]
> **Pipeline Smoke Test Only** (n_slices=1 < 5).
> These results are NOT statistically meaningful.
> Do NOT use these weights in production.

> [!CAUTION]
> **Non-strict PIT data**. Financial data uses 90-day buffer,
> not true disclosure-date PIT. Results may contain look-ahead bias.
> Production weight updates require strict PIT back-validation.

## Pareto Front

| # | Rank IC | Excess Return | Disaster Rate | growth_acceleration | growth_quality | market_confirmation | valuation_constraint |
|---|---------|---------------|---------------|---|---|---|---|
| 1 | 0.2168 | 0.1542 | 0.0000 | 5.0 | 60.0 | 24.15 | 10.85 |
| 2 | 0.2220 | -0.0032 | 0.0000 | 5.0 | 37.46 | 41.42 | 16.12 |
| 3 | 0.2165 | 0.2317 | 0.0000 | 10.05 | 60.0 | 17.76 | 12.2 |
| 4 | 0.2057 | 0.3127 | 0.0000 | 5.0 | 60.0 | 30.0 | 5.0 |
| 5 | 0.2057 | 0.3127 | 0.0000 | 5.0 | 60.0 | 30.0 | 5.0 |
| 6 | 0.1811 | 0.6635 | 0.0000 | 30.0 | 60.0 | 5.0 | 5.0 |
| 7 | 0.2057 | 0.3127 | 0.0000 | 5.0 | 60.0 | 5.0 | 5.0 |
| 8 | 0.1848 | 0.5880 | 0.0000 | 18.2 | 23.64 | 46.16 | 12.0 |
| 9 | 0.1407 | 2.0153 | 0.0000 | 18.05 | 19.3 | 22.97 | 39.67 |

## Approval Status

- [ ] Reviewed by strategy owner
- [ ] Back-validated on strict PIT data (required before production update)
- [ ] Risk committee sign-off
