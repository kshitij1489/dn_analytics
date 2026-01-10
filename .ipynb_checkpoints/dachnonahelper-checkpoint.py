import json
import requests
import pandas as pd
from typing import Optional, Iterable


BASE_URL = "https://webhooks.db1-prod-dachnona.store/analytics"
API_KEY = "f3e1753aa4c44159fa7218a31cd8db1e"

HEADERS = {
    "X-API-Key": API_KEY,
}

JSON_COLUMNS = {
    "orders": ["raw_event"],
    "order-items": ["raw_item"],
    "addons": ["raw_addon"],
    "discounts": ["raw_discount"],
}

def _safe_json_load(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value

def fetch_stream(
    endpoint: str,
    limit: int = 500,
    start_cursor: Optional[int] = 0,
) -> pd.DataFrame:
    rows = []
    last_stream_id = start_cursor or 0

    while True:
        params = {
            "limit": limit,
            "cursor": last_stream_id,   # ✅ correct param
        }

        resp = requests.get(
            f"{BASE_URL}/{endpoint}/",
            headers=HEADERS,
            params=params,
            timeout=60,
        )
        resp.raise_for_status()

        payload = resp.json()
        batch = payload.get("data", [])
        if not batch:
            break

        rows.extend(batch)

        # advance cursor using the last row actually returned
        last_stream_id = batch[-1]["stream_id"]

        # ✅ CRITICAL: stop when fewer than limit rows are returned
        if not batch:
            break

    df = pd.DataFrame(rows)

    for col in JSON_COLUMNS.get(endpoint, []):
        if col in df.columns:
            df[col] = df[col].apply(_safe_json_load)

    return df


def fetch_petpooja_data(
    limit: int = 500,
    start_cursor: Optional[int] = 0
) -> pd.DataFrame:
    """
    Fetch orders stream and safely parse JSON columns.
    """
    return fetch_stream(
        endpoint="orders",
        limit=limit,
        start_cursor=start_cursor
    )

def _extract_order_fields(e):
    if not isinstance(e, dict):
        return None, None
    order = (
        e.get("raw_payload", {})
         .get("properties", {})
         .get("Order", {})
    )
    return order.get("orderID"), order.get("created_on")


def dedupe_latest_orders(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only the latest snapshot per order_id.
    Assumes each payload is a full replacement.
    """

    if "raw_event" not in df.columns:
        raise ValueError("Expected 'raw_event' column")

    out = df.copy()

    # ---- Extract once ----
    out[["_order_id", "_created_on"]] = out["raw_event"].apply(
        lambda e: pd.Series(_extract_order_fields(e))
    )

    # ---- Clean ----
    out["_created_on"] = pd.to_datetime(out["_created_on"], errors="coerce")
    out = out[out["_order_id"].notna()]

    # ---- Deduplicate ----
    out = (
        out
        .sort_values("_created_on")
        .drop_duplicates(subset=["_order_id"], keep="last")
        .drop(columns=["_order_id", "_created_on"])
    )

    return out

def normalize_orders(orders_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Normalize raw order events into structured DataFrames.
    """

    if "raw_event" not in orders_df.columns:
        raise ValueError("orders_df must contain a 'raw_event' column")

    events = orders_df["raw_event"].dropna()
    restaurants = {}
    orders, customers = [], []
    order_items, addons = [], []
    discounts, taxes = [], []

    customer_id = 1
    order_item_id = 1
    addon_id = 1
    discount_id = 1
    tax_id = 1

    for event in events:
        payload = event.get("raw_payload", {})
        props = payload.get("properties", {})
        if not props:
            continue

        # ---------- Restaurant ----------
        r = props.get("Restaurant", {})
        restID = r.get("restID")
        if restID and restID not in restaurants:
            restaurants[restID] = {
                "restID": restID,
                "address": r.get("address"),
                "res_name": r.get("res_name"),
                "contact_information": r.get("contact_information")
            }

        # ---------- Order ----------
        o = props.get("Order", {})
        order_id = o.get("orderID")
        if not order_id:
            continue

        # Convert created_on ONCE
        order_created_on = pd.to_datetime(
            o.get("created_on"), errors="coerce"
        )

        # ---------- Customer ----------
        c = props.get("Customer", {})
        customers.append({
            "customer_id": customer_id,
            "order_id": order_id,
            "order_from": o.get("order_from"),
            "order_created_on": order_created_on,
            "name": c.get("name"),
            "gstin": c.get("gstin"),
            "phone": c.get("phone"),
            "address": c.get("address")
        })

        orders.append({
            "order_id": order_id,
            "restID": restID,
            "customer_id": customer_id,
            **o,
            "created_on": order_created_on
        })
        customer_id += 1

        # ---------- Order Items ----------
        for item in props.get("OrderItem", []):
            order_items.append({
                "order_item_id": order_item_id,
                "order_id": order_id,
                "order_created_on": order_created_on,
                "itemid": item.get("itemid"),
                "itemcode": item.get("itemcode"),
                "name": item.get("name"),
                "category_name": item.get("category_name"),
                "quantity": item.get("quantity"),
                "price": item.get("price"),
                "discount": item.get("discount"),
                "tax": item.get("tax"),
                "total": item.get("total"),
                "specialnotes": item.get("specialnotes"),
                "vendoritemcode": item.get("vendoritemcode")
            })

            for a in item.get("addon", []):
                addons.append({
                    "order_item_addon_id": addon_id,
                    "order_item_id": order_item_id,
                    "order_id": order_id,
                    "order_created_on": order_created_on,
                    "addon_id": a.get("addon_id"),
                    "name": a.get("name"),
                    "group_name": a.get("group_name"),
                    "addon_group_id": a.get("addon_group_id"),
                    "price": a.get("price"),
                    "quantity": a.get("quantity"),
                    "sap_code": a.get("sap_code")
                })
                addon_id += 1

            order_item_id += 1

        # ---------- Discounts ----------
        for d in props.get("Discount", []):
            discounts.append({
                "discount_id": discount_id,
                "order_id": order_id,
                "order_created_on": order_created_on,
                "rate": d.get("rate"),
                "type": d.get("type"),
                "title": d.get("title"),
                "amount": d.get("amount")
            })
            discount_id += 1

        # ---------- Taxes ----------
        for t in props.get("Tax", []):
            taxes.append({
                "tax_id": tax_id,
                "order_id": order_id,
                "order_created_on": order_created_on,
                "rate": t.get("rate"),
                "type": t.get("type"),
                "title": t.get("title"),
                "amount": t.get("amount")
            })
            tax_id += 1

    return {
        "restaurants": pd.DataFrame(restaurants.values()),
        "orders": pd.DataFrame(orders),
        "customers": pd.DataFrame(customers),
        "order_items": pd.DataFrame(order_items),
        "addons": pd.DataFrame(addons),
        "discounts": pd.DataFrame(discounts),
        "taxes": pd.DataFrame(taxes),
    }

def prepare_orders_data():
    orders_df = fetch_petpooja_data()
    return normalize_orders(dedupe_latest_orders(orders_df))

def add_order_date(df_orders: pd.DataFrame) -> pd.DataFrame:
    df = df_orders.copy()
    #df["created_on"] = pd.to_datetime(df["created_on"], errors="coerce")
    df = df[df["created_on"].notna()]
    df["order_date"] = df["created_on"].dt.date
    return df

def daily_aggregate(
    df: pd.DataFrame,
    metrics: dict,
    group_cols: list[str] = ["order_date"]
) -> pd.DataFrame:
    """
    Generic daily aggregation helper.
    """
    return (
        df
        .groupby(group_cols, as_index=False)
        .agg(**metrics)
        .sort_values(group_cols)
    )

def daily_channel_revenue(df_orders: pd.DataFrame) -> pd.DataFrame:
    return (
        df_orders
        .groupby(["order_date", "order_from"])
        .agg(revenue=("total", "sum"))
        .reset_index()
        .pivot(index="order_date", columns="order_from", values="revenue")
        .fillna(0)
        .reset_index()
    )

def daily_item_sales(order_items_df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean item names and compute daily item-level sales metrics.

    Returns:
        DataFrame with:
        - order_date
        - item_name
        - items_sold
        - total_sales
    """
    df = order_items_df.copy()

    # ---- Validate required columns ----
    required_cols = {"name", "quantity", "total", "order_created_on"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # ---- Clean item names ----
    df["item_name"] = (
        df["name"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    # ---- Ensure datetime ----
    df["order_created_on"] = pd.to_datetime(
        df["order_created_on"], errors="coerce"
    )

    df = df[df["order_created_on"].notna()]

    # ---- Extract date ----
    df["order_date"] = df["order_created_on"].dt.date

    # ---- Daily aggregation ----
    result = (
        df
        .groupby(["order_date", "item_name"], as_index=False)
        .agg(
            items_sold=("quantity", "sum"),
            total_sales=("total", "sum"),
        )
        .sort_values(["order_date", "items_sold"], ascending=[True, False])
    )

    return result
