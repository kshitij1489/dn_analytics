import sys
from pathlib import Path
import traceback

# Ensure we can import from root level modules (database, utils, etc)
# This assumes the app is run from the project root or src/core is part of a larger package structure that includes these.
# For now, we'll keep the imports as they match the existing project structure.

from services.load_orders import (
    get_last_stream_id,
    process_order,
    create_schema_if_needed
)
from utils.api_client import fetch_stream_raw
from services.clustering_service import OrderItemCluster
from scripts.seed_from_backups import export_to_backups, perform_seeding

class SyncStatus:
    def __init__(self, type, message=None, progress=0.0, current=0, total=0, stats=None):
        self.type = type # 'info', 'progress', 'done', 'error'
        self.message = message
        self.progress = progress
        self.current = current
        self.total = total
        self.stats = stats

def sync_database(conn, cluster=None):
    """
    Sync database with incremental updates.
    Yields SyncStatus objects to communicate progress.
    """
    try:
        # Check if schema exists (SQLite uses sqlite_master)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='orders'
        """)
        tables_exist = cursor.fetchone() is not None
        cursor.close()
        
        if not tables_exist:
            try:
                create_schema_if_needed(conn)
                yield SyncStatus('info', "📋 Database schema created.")
            except Exception as schema_error:
                yield SyncStatus('error', f"Schema creation failed: {str(schema_error)}")
                return
        
        # Auto-seed menu data from backups if menu_items table is empty (happens FIRST on empty DB)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM menu_items")
        menu_count = cursor.fetchone()[0]
        cursor.close()
        
        if menu_count == 0:
            yield SyncStatus('info', "🌱 Seeding menu data from backup files...")
            try:
                if perform_seeding(conn):
                    yield SyncStatus('info', "✅ Menu data seeded successfully!")
                else:
                    yield SyncStatus('info', "⚠️ Menu seeding skipped (no backup files found)")
            except Exception as seed_error:
                yield SyncStatus('info', f"⚠️ Menu seeding failed: {str(seed_error)}")
        
        # Check if customers table is empty to determine sync cursor
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customers")
        customer_count = cursor.fetchone()[0]
        cursor.close()
        
        start_cursor = 0
        if customer_count == 0:
            yield SyncStatus('info', "🔄 Customers table empty - performing full reload...")
            start_cursor = 0
        else:
            start_cursor = get_last_stream_id(conn)
        
        # Fetch orders
        new_orders, total_available = fetch_stream_raw(
            conn,
            endpoint="orders",
            start_cursor=start_cursor
        )
        
        if not new_orders:
            yield SyncStatus('done', "No new orders to sync", progress=1.0, stats={'count': 0, 'fetched': 0, 'total_available': total_available})
            return
        
        # Initialize Cluster if not provided
        if cluster is None:
            cluster = OrderItemCluster(conn)
        
        stats = {
            'orders': 0,
            'order_items': 0,
            'order_item_addons': 0,
            'order_taxes': 0,
            'order_discounts': 0,
            'errors': [],
            'fetched': len(new_orders),
            'total_available': total_available
        }
        
        total_orders = len(new_orders)
        
        for i, order_payload in enumerate(new_orders):
            progress_pct = (i + 1) / total_orders
            yield SyncStatus(
                'progress', 
                f"Processing order {i+1}/{total_orders}...", 
                progress=progress_pct, 
                current=i+1, 
                total=total_orders
            )
            
            order_stats = process_order(conn, order_payload, cluster)
            for key in stats:
                if key not in order_stats:
                    continue
                if key == 'errors':
                    stats[key].extend(order_stats[key])
                else:
                    stats[key] += order_stats[key]
        
        # Export to backups
        if len(new_orders) > 0:
             export_to_backups(conn)
            
        yield SyncStatus('done', "Sync Complete", progress=1.0, stats=stats, total=total_orders)
        
    except Exception as e:
        yield SyncStatus('error', f"Sync error: {str(e)}\n{traceback.format_exc()}")
