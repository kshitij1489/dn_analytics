"""
Reorder utility functions for calculating customer and item reorder metrics.
These functions are designed to be reusable across different parts of the application.
"""
from datetime import datetime
from typing import Set, Dict


def get_returning_customer_ids(conn, date: str) -> Set[int]:
    """
    Get IDs of verified customers who ordered on the given date AND have ordered before that date.
    
    Args:
        conn: SQLite connection
        date: Date string in 'YYYY-MM-DD' format
        
    Returns:
        Set of customer_ids who are returning customers for that date
    """
    query = """
        SELECT DISTINCT o.customer_id
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        WHERE c.is_verified = 1
          AND o.order_status = 'Success'
          AND DATE(o.created_on) = ?
          AND EXISTS (
              SELECT 1 FROM orders prev
              WHERE prev.customer_id = o.customer_id
                AND prev.order_status = 'Success'
                AND DATE(prev.created_on) < ?
          )
    """
    cursor = conn.execute(query, (date, date))
    return {row[0] for row in cursor.fetchall()}


def get_reorder_item_counts(conn, date: str) -> Dict[str, int]:
    """
    Get count of verified customers who ordered each menu item on the given date
    AND have ordered that same item before.
    
    Args:
        conn: SQLite connection
        date: Date string in 'YYYY-MM-DD' format
        
    Returns:
        Dict mapping menu_item_id to reorder count
    """
    query = """
        SELECT 
            oi.menu_item_id,
            COUNT(DISTINCT o.customer_id) as reorder_count
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        JOIN customers c ON o.customer_id = c.customer_id
        WHERE c.is_verified = 1
          AND o.order_status = 'Success'
          AND DATE(o.created_on) = ?
          AND oi.menu_item_id IS NOT NULL
          AND EXISTS (
              SELECT 1 
              FROM order_items prev_oi
              JOIN orders prev_o ON prev_oi.order_id = prev_o.order_id
              WHERE prev_o.customer_id = o.customer_id
                AND prev_o.order_status = 'Success'
                AND DATE(prev_o.created_on) < ?
                AND prev_oi.menu_item_id = oi.menu_item_id
          )
        GROUP BY oi.menu_item_id
    """
    cursor = conn.execute(query, (date, date))
    return {row[0]: row[1] for row in cursor.fetchall()}


def is_returning_customer(conn, customer_id: int, before_date: str) -> bool:
    """
    Check if a customer has placed any order before the given date.
    
    Args:
        conn: SQLite connection
        customer_id: The customer to check
        before_date: Date string in 'YYYY-MM-DD' format
        
    Returns:
        True if customer has orders before that date
    """
    query = """
        SELECT 1 FROM orders
        WHERE customer_id = ?
          AND order_status = 'Success'
          AND DATE(created_on) < ?
        LIMIT 1
    """
    cursor = conn.execute(query, (customer_id, before_date))
    return cursor.fetchone() is not None
