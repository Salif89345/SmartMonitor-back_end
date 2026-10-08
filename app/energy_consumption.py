"""Conservative counter deltas; never infer energy from instantaneous power."""

from datetime import datetime, timedelta, timezone
from math import isfinite


def consumption_buckets(measurements, *, period_from, period_to,
                        resolution_seconds, max_gap_seconds, baseline=None):
    """Return known kWh, partial flags and a partial-period flag.

    A delta is assigned to the bucket containing its later reading. This is
    endpoint accounting at the sampling precision, not interpolated energy.
    Missing/invalid samples and gaps > 3 persistence intervals break continuity.
    A decrease and the following interval are excluded: a reset cannot be
    distinguished from a transient bad reading without a trusted reset marker.
    The next valid reading establishes a fresh reference, avoiding both an old
    high-water lock and a fictitious rebound after one low outlier.
    """
    start = period_from.astimezone(timezone.utc)
    end = period_to.astimezone(timezone.utc)
    totals, partial = {}, {}
    previous = baseline
    recovering = False
    last_seen = None
    period_partial = False
    for measured_at, value in measurements:
        stamp = measured_at.astimezone(timezone.utc)
        if not start <= stamp < end:
            continue
        bucket = start + timedelta(seconds=(
            int((stamp - start).total_seconds()) // resolution_seconds
        ) * resolution_seconds)
        totals.setdefault(bucket, None)
        partial.setdefault(bucket, False)
        if last_seen is not None and stamp <= last_seen:
            partial[bucket] = period_partial = True
            continue
        last_seen = stamp
        if value is None or not isfinite(value) or value < 0:
            previous = None
            recovering = False
            partial[bucket] = period_partial = True
            continue
        if previous is None:
            partial[bucket] = period_partial = True
        else:
            previous_stamp, previous_value = previous
            elapsed = (stamp - previous_stamp.astimezone(timezone.utc)).total_seconds()
            if (previous_value is None or not isfinite(previous_value)
                    or previous_value < 0 or not 0 < elapsed <= max_gap_seconds):
                partial[bucket] = period_partial = True
                recovering = False
            elif value < previous_value:
                partial[bucket] = period_partial = True
                recovering = True
            elif recovering:
                partial[bucket] = period_partial = True
                recovering = False
            else:
                totals[bucket] = (totals[bucket] or 0.0) + value - previous_value
        previous = (stamp, value)
    if last_seen is None or (end - last_seen).total_seconds() > max_gap_seconds:
        period_partial = True
        if last_seen is not None:
            partial[bucket] = True
    return ({key: None if value is None else round(value, 12)
             for key, value in totals.items()}, partial, period_partial)
