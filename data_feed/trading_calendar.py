# -*- coding: utf-8 -*-
"""Chinese futures trading-day helpers.

Night sessions belong to the following trading day.  When a complete timestamp
series is available, observed day sessions are used so weekends and exchange
holidays are handled without a hard-coded holiday calendar.  Single-timestamp
fallbacks use business days and are only used when the data source did not
provide an authoritative ``trading_date`` field.
"""

from __future__ import annotations

import pandas as pd


DAY_SESSION_START_HOUR = 8
DAY_SESSION_END_HOUR = 18
NIGHT_SESSION_START_HOUR = 21


def fallback_trading_date(value) -> pd.Timestamp:
    """Infer one trading date when no source-provided mapping is available."""
    timestamp = pd.Timestamp(value)
    calendar_date = timestamp.normalize()
    if timestamp.hour >= NIGHT_SESSION_START_HOUR:
        return calendar_date + pd.offsets.BDay(1)
    if timestamp.hour < DAY_SESSION_START_HOUR and calendar_date.weekday() >= 5:
        return calendar_date + pd.offsets.BDay(1)
    return calendar_date


def infer_trading_dates(values) -> pd.Series:
    """Return normalized trading dates aligned to ``values``.

    Pre-midnight night bars are mapped to the next observed day session.  Early
    morning bars reuse the mapping of the preceding night when that night is
    present.  This maps Friday night and Saturday early morning to Monday, and
    also respects exchange holidays represented by gaps in the source data.
    """
    source = values if isinstance(values, pd.Series) else pd.Series(values)
    datetimes = pd.to_datetime(source, errors="coerce")
    result = pd.Series(
        datetimes.dt.normalize().to_numpy(copy=True),
        index=source.index,
        dtype='datetime64[ns]',
    )
    valid = datetimes.notna()
    if not valid.any():
        return result

    day_mask = valid & datetimes.dt.hour.between(
        DAY_SESSION_START_HOUR, DAY_SESSION_END_HOUR, inclusive="both"
    )
    observed_day_sessions = pd.DatetimeIndex(result.loc[day_mask].dropna().unique()).sort_values()

    night_dates = pd.DatetimeIndex(
        result.loc[valid & datetimes.dt.hour.ge(NIGHT_SESSION_START_HOUR)].dropna().unique()
    ).sort_values()
    night_mapping: dict[pd.Timestamp, pd.Timestamp] = {}
    for night_date in night_dates:
        later_sessions = observed_day_sessions[observed_day_sessions > night_date]
        target = later_sessions[0] if len(later_sessions) else night_date + pd.offsets.BDay(1)
        night_mapping[pd.Timestamp(night_date)] = pd.Timestamp(target).normalize()

    night_mask = valid & datetimes.dt.hour.ge(NIGHT_SESSION_START_HOUR)
    if night_mask.any():
        result.loc[night_mask] = result.loc[night_mask].map(night_mapping)

    early_mask = valid & datetimes.dt.hour.lt(DAY_SESSION_START_HOUR)
    if early_mask.any():
        previous_calendar_dates = datetimes.loc[early_mask].dt.normalize() - pd.Timedelta(days=1)
        mapped = previous_calendar_dates.map(night_mapping)
        fallback = datetimes.loc[early_mask].map(fallback_trading_date)
        result.loc[early_mask] = mapped.where(mapped.notna(), fallback)

    return pd.to_datetime(result, errors="coerce").dt.normalize()
