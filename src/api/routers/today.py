"""
Today's Snapshot API Router
Provides real-time daily metrics for the Today dashboard.
"""
from datetime import date
from fastapi import APIRouter, Depends
from src.api.dependencies import get_db
from src.core.utils.reorder_utils import get_returning_customer_ids, get_reorder_item_counts

router = APIRouter(prefix="/api/today", tags=["today"])


@router.get("/summary")
def get_today_summary(conn=Depends(get_db)):
    """
    Get today's summary: revenue, orders by source, reorder customer count.
    """
    today_str = date.today().isoformat()
    
    # Total revenue and orders
    totals_query = """
        SELECT 
            COALESCE(SUM(total), 0) as total_revenue,
            COUNT(*) as total_orders
        FROM orders
        WHERE order_status = 'Success'
          AND DATE(created_on) = ?
    """
    cursor = conn.execute(totals_query, (today_str,))
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
          AND DATE(created_on) = ?
        GROUP BY order_from
        ORDER BY revenue DESC
    """
    cursor = conn.execute(source_query, (today_str,))
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
          AND DATE(created_on) = ?
          AND customer_id IS NOT NULL
    """
    cursor = conn.execute(unique_customers_query, (today_str,))
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
def get_today_menu_items(conn=Depends(get_db)):
    """
    Get menu items sold today with quantities and reorder counts.
    """
    today_str = date.today().isoformat()
    
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
          AND DATE(o.created_on) = ?
        GROUP BY oi.menu_item_id, item_name, category
        ORDER BY qty_sold DESC
    """
    cursor = conn.execute(query, (today_str,))
    
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
def get_today_customers(conn=Depends(get_db)):
    """
    Get customer list for today with order details.
    Sorted: verified customers first, then by order value descending.
    """
    today_str = date.today().isoformat()
    
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
              AND DATE(o.created_on) = ?
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
              AND DATE(o.created_on) = ?
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
    cursor = conn.execute(query, (today_str, today_str))
    
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
