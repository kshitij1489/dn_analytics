"""
Today's Snapshot API Router
Provides real-time daily metrics for the Today dashboard.
"""
from datetime import date as DateType
from typing import Optional
from fastapi import APIRouter, Depends, Query
from src.api.dependencies import get_db
from src.core.utils.reorder_utils import get_returning_customer_ids, get_reorder_item_counts
from src.core.utils.business_date import get_current_business_date, get_business_date_range

router = APIRouter(prefix="/api/today", tags=["today"])


@router.get("/summary")
def get_today_summary(
    date: Optional[DateType] = Query(None, description="Date in YYYY-MM-DD format"),
    conn=Depends(get_db)
):
    """
    Get today's summary: revenue, orders by source, reorder customer count.
    If date is provided, returns summary for that specific date.
    """
    today_str = date.isoformat() if date else get_current_business_date()
    start_dt, end_dt = get_business_date_range(today_str)
    
    # Total revenue and orders
    totals_query = """
        SELECT 
            COALESCE(SUM(total), 0) as total_revenue,
            COUNT(*) as total_orders
        FROM orders
        WHERE order_status = 'Success'
          AND created_on >= ? AND created_on <= ?
    """
    cursor = conn.execute(totals_query, (start_dt, end_dt))
    row = cursor.fetchone()
    total_revenue = row[0] if row else 0
    total_orders = row[1] if row else 0
    
    # Breakdown by source
    source_query = """
        SELECT 
            order_from,
            COUNT(*) as order_count,
            COALESCE(SUM(total), 0) as revenue
        FROM orders
        WHERE order_status = 'Success'
          AND created_on >= ? AND created_on <= ?
        GROUP BY order_from
        ORDER BY revenue DESC
    """
    cursor = conn.execute(source_query, (start_dt, end_dt))
    sources = [
        {"source": row[0], "orders": row[1], "revenue": float(row[2])}
        for row in cursor.fetchall()
    ]
    
    # Reorder customer count
    returning_customers = get_returning_customer_ids(conn, today_str)
    
    # Total unique customers today
    unique_customers_query = """
        SELECT COUNT(DISTINCT customer_id)
        FROM orders
        WHERE order_status = 'Success'
          AND created_on >= ? AND created_on <= ?
          AND customer_id IS NOT NULL
    """
    cursor = conn.execute(unique_customers_query, (start_dt, end_dt))
    total_customers = cursor.fetchone()[0] or 0
    
    return {
        "date": today_str,
        "total_revenue": float(total_revenue),
        "total_orders": total_orders,
        "total_customers": total_customers,
        "returning_customer_count": len(returning_customers),
        "sources": sources
    }


@router.get("/menu-items")
def get_today_menu_items(
    date: Optional[DateType] = Query(None, description="Date in YYYY-MM-DD format"),
    conn=Depends(get_db)
):
    """
    Get menu items sold today with quantities and reorder counts.
    If date is provided, returns items for that specific date.
    """
    today_str = date.isoformat() if date else get_current_business_date()
    start_dt, end_dt = get_business_date_range(today_str)
    
    # Get reorder counts for all items
    reorder_counts = get_reorder_item_counts(conn, today_str)
    
    # Menu items sold today
    query = """
        SELECT 
            oi.menu_item_id,
            COALESCE(mi.name, oi.name_raw) as item_name,
            COALESCE(mi.type, oi.category_name, 'Uncategorized') as category,
            SUM(oi.quantity) as qty_sold,
            SUM(oi.total_price) as revenue
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        LEFT JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        WHERE o.order_status = 'Success'
          AND o.created_on >= ? AND o.created_on <= ?
        GROUP BY oi.menu_item_id, item_name, category
        ORDER BY qty_sold DESC
    """
    cursor = conn.execute(query, (start_dt, end_dt))
    
    items = []
    for row in cursor.fetchall():
        menu_item_id = row[0]
        items.append({
            "menu_item_id": menu_item_id,
            "item_name": row[1],
            "cluster_name": row[2],
            "qty_sold": row[3],
            "revenue": float(row[4]) if row[4] else 0,
            "reorder_count": reorder_counts.get(menu_item_id, 0) if menu_item_id else 0
        })
    
    return {"date": today_str, "items": items}


@router.get("/customers")
def get_today_customers(
    date: Optional[DateType] = Query(None, description="Date in YYYY-MM-DD format"),
    conn=Depends(get_db)
):
    """
    Get customer list for today with order details.
    Sorted: verified customers first, then by order value descending.
    If date is provided, returns customers for that specific date.
    """
    today_str = date.isoformat() if date else get_current_business_date()
    start_dt, end_dt = get_business_date_range(today_str)
    
    # Get returning customer IDs
    returning_ids = get_returning_customer_ids(conn, today_str)
    
    # Customer orders today - use subquery for correct totals
    query = """
        WITH customer_orders AS (
            SELECT 
                o.customer_id,
                SUM(o.total) as order_value
            FROM orders o
            WHERE o.order_status = 'Success'
              AND o.created_on >= ? AND o.created_on <= ?
            GROUP BY o.customer_id
        ),
        customer_items AS (
            SELECT 
                o.customer_id,
                GROUP_CONCAT(DISTINCT COALESCE(mi.name, oi.name_raw)) as items_ordered
            FROM orders o
            LEFT JOIN order_items oi ON o.order_id = oi.order_id
            LEFT JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
            WHERE o.order_status = 'Success'
              AND o.created_on >= ? AND o.created_on <= ?
            GROUP BY o.customer_id
        )
        SELECT 
            co.customer_id,
            COALESCE(c.name, 'Anonymous') as customer_name,
            c.is_verified,
            co.order_value,
            ci.items_ordered,
            COALESCE(c.total_orders, 0) as history_orders,
            COALESCE(c.total_spent, 0) as history_spent
        FROM customer_orders co
        LEFT JOIN customers c ON co.customer_id = c.customer_id
        LEFT JOIN customer_items ci ON co.customer_id = ci.customer_id
        ORDER BY 
            c.is_verified DESC,
            co.order_value DESC
    """
    cursor = conn.execute(query, (start_dt, end_dt, start_dt, end_dt))
    
    customers = []
    for row in cursor.fetchall():
        customer_id = row[0]
        items_str = row[4] or ""
        customers.append({
            "customer_id": customer_id,
            "name": row[1],
            "is_verified": bool(row[2]),
            "order_value": float(row[3]) if row[3] else 0,
            "items_ordered": items_str.split(",") if items_str else [],
            "is_returning": customer_id in returning_ids if customer_id else False,
            "history_orders": row[5] or 0,
            "history_spent": float(row[6]) if row[6] else 0
        })
    
    return {"date": today_str, "customers": customers}


@router.get("/orders")
def get_today_orders(
    date: Optional[DateType] = Query(None, description="Date in YYYY-MM-DD format"),
    conn=Depends(get_db)
):
    """
    Get all orders for today with customer name, item details, and order info.
    Items include quantity and '-Repeat' label if customer ordered this item before.
    """
    today_str = date.isoformat() if date else get_current_business_date()
    start_dt, end_dt = get_business_date_range(today_str)
    
    # Get all orders for the day
    orders_query = """
        SELECT 
            o.order_id,
            o.petpooja_order_id,
            o.customer_id,
            COALESCE(c.name, 'Anonymous') as customer_name,
            o.total,
            o.created_on,
            o.order_from
        FROM orders o
        LEFT JOIN customers c ON o.customer_id = c.customer_id
        WHERE o.order_status = 'Success'
          AND o.created_on >= ? AND o.created_on <= ?
        ORDER BY o.created_on DESC
    """
    cursor = conn.execute(orders_query, (start_dt, end_dt))
    order_rows = cursor.fetchall()
    
    if not order_rows:
        return {"date": today_str, "orders": []}
    
    order_ids = [row[0] for row in order_rows]
    customer_ids = {row[2] for row in order_rows if row[2]}
    
    # Get items for all orders
    placeholders = ','.join('?' * len(order_ids))
    items_query = f"""
        SELECT 
            oi.order_id,
            oi.menu_item_id,
            COALESCE(mi.name, oi.name_raw) as item_name,
            oi.quantity
        FROM order_items oi
        LEFT JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        WHERE oi.order_id IN ({placeholders})
    """
    cursor = conn.execute(items_query, order_ids)
    
    # Group items by order_id
    order_items_map = {}
    for row in cursor.fetchall():
        order_id = row[0]
        if order_id not in order_items_map:
            order_items_map[order_id] = []
        order_items_map[order_id].append({
            "menu_item_id": row[1],
            "item_name": row[2],
            "quantity": row[3]
        })
    
    # Get historical item purchases per customer (before this date)
    customer_item_history = {}
    if customer_ids:
        cust_placeholders = ','.join('?' * len(customer_ids))
        history_query = f"""
            SELECT DISTINCT
                o.customer_id,
                oi.menu_item_id
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.customer_id IN ({cust_placeholders})
              AND o.order_status = 'Success'
              AND o.created_on < ?
              AND oi.menu_item_id IS NOT NULL
        """
        cursor = conn.execute(history_query, list(customer_ids) + [start_dt])
        for row in cursor.fetchall():
            cust_id, item_id = row[0], row[1]
            if cust_id not in customer_item_history:
                customer_item_history[cust_id] = set()
            customer_item_history[cust_id].add(item_id)
    
    # Format orders
    orders = []
    for row in order_rows:
        order_id = row[0]
        customer_id = row[2]
        items = order_items_map.get(order_id, [])
        cust_history = customer_item_history.get(customer_id, set())
        
        # Format items with qty and repeat label
        formatted_items = []
        for item in items:
            item_name = item["item_name"]
            qty = item["quantity"]
            menu_item_id = item["menu_item_id"]
            
            # Add quantity if > 1
            display_name = f"{item_name}({qty})" if qty > 1 else item_name
            
            # Add repeat label if customer ordered this item before
            if menu_item_id and menu_item_id in cust_history:
                display_name += "-Repeat"
            
            formatted_items.append(display_name)
        
        # Extract time from created_on (handles both "YYYY-MM-DD HH:MM:SS" and ISO "YYYY-MM-DDTHH:MM:SS")
        created_on_str = row[5] or ""
        order_time = created_on_str.replace("T", " ").split(" ")[1][:5] if created_on_str else ""
        
        orders.append({
            "order_id": order_id,
            "petpooja_order_id": row[1],
            "customer_id": customer_id,
            "customer_name": row[3],
            "order_items": formatted_items,
            "total": float(row[4]) if row[4] else 0,
            "time": order_time,
            "source": row[6]
        })
    
    return {"date": today_str, "orders": orders}

