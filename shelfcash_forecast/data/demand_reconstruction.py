from __future__ import annotations

import pandas as pd


def reconstruct_demand(panel: pd.DataFrame) -> pd.DataFrame:
    """Create a transparent demand proxy without inventing lost sales."""

    frame = panel.copy()
    stockout_known = frame["is_stockout"].notna()
    stockout_true = frame["is_stockout"].fillna(False).astype(bool)

    frame["demand_proxy"] = frame["quantity_sold"].astype("Float64")
    frame["target_is_censored"] = frame["store_open"] & stockout_known & stockout_true
    frame["target_quality"] = "observed"

    frame.loc[~frame["store_open"], "target_quality"] = "store_closed"
    frame.loc[frame["target_is_censored"], "target_quality"] = "stockout_censored"
    frame.loc[
        frame["store_open"] & ~frame["row_observed"],
        "target_quality",
    ] = "missing_open_day"
    frame.loc[
        frame["store_open"] & frame["row_observed"] & frame["is_stockout"].isna(),
        "target_quality",
    ] = "stockout_unknown"

    frame["train_eligible"] = (
        frame["store_open"]
        & frame["demand_proxy"].notna()
        & ~frame["target_is_censored"]
    )

    return frame
