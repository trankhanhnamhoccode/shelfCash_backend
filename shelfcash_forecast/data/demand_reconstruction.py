from __future__ import annotations

import pandas as pd


def reconstruct_demand(panel: pd.DataFrame) -> pd.DataFrame:
    """Create a transparent demand proxy without inventing lost sales."""

    frame = panel.copy()
    direct_known = frame["is_stockout"].notna()
    inventory_known = frame["is_available"].notna() if "is_available" in frame else pd.Series(False, index=frame.index)
    inferred_stockout = inventory_known & ~frame["is_available"].fillna(True).astype(bool)
    stockout_known = direct_known | inventory_known
    stockout_true = frame["is_stockout"].fillna(False).astype(bool) | (~direct_known & inferred_stockout)
    frame["stockout_reconstruction_source"] = "none"
    frame.loc[direct_known, "stockout_reconstruction_source"] = "is_stockout"
    frame.loc[~direct_known & inventory_known, "stockout_reconstruction_source"] = "inventory_availability"
    frame["stockout_reconstruction_confidence"] = 0.0
    frame.loc[direct_known, "stockout_reconstruction_confidence"] = 1.0
    frame.loc[~direct_known & inventory_known, "stockout_reconstruction_confidence"] = 0.8
    frame["effective_is_stockout"] = stockout_true.astype("boolean")

    frame["demand_proxy"] = frame["quantity_sold"].astype("Float64")
    uncensored_observed = frame["quantity_sold"].where(~stockout_true)
    prior_typical = uncensored_observed.groupby(
        [frame["store_key"], frame["product_key"]], observed=True
    ).transform(lambda values: values.shift(1).rolling(28, min_periods=4).median())
    reconstructable = frame["store_open"] & stockout_known & stockout_true & prior_typical.notna()
    frame.loc[reconstructable, "demand_proxy"] = frame.loc[reconstructable, ["quantity_sold"]].max(axis=1).combine(
        prior_typical.loc[reconstructable], max
    )
    frame.loc[reconstructable & direct_known, "stockout_reconstruction_source"] = "is_stockout_historical_median"
    frame.loc[reconstructable & ~direct_known, "stockout_reconstruction_source"] = "inventory_historical_median"
    frame.loc[reconstructable & direct_known, "stockout_reconstruction_confidence"] = 0.8
    frame.loc[reconstructable & ~direct_known, "stockout_reconstruction_confidence"] = 0.6
    frame["target_is_censored"] = frame["store_open"] & stockout_known & stockout_true & ~reconstructable
    frame["target_quality"] = "observed"

    frame.loc[~frame["store_open"], "target_quality"] = "store_closed"
    frame.loc[frame["target_is_censored"], "target_quality"] = "stockout_censored"
    frame.loc[reconstructable, "target_quality"] = "stockout_reconstructed"
    frame.loc[
        frame["store_open"] & ~frame["row_observed"],
        "target_quality",
    ] = "missing_open_day"
    frame.loc[
        frame["store_open"] & frame["row_observed"] & ~stockout_known,
        "target_quality",
    ] = "stockout_unknown"

    frame["train_eligible"] = (
        frame["store_open"]
        & frame["demand_proxy"].notna()
        & ~frame["target_is_censored"]
    )

    return frame
