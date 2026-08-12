import sys
from pathlib import Path
import traceback

# Ensure we can import from root level modules (database, utils, etc)
# This assumes the app is run from the project root or src/core is part of a larger package structure that includes these.
# For now, we'll keep the imports as they match the existing project structure.

from services.load_orders import (
    get_last_stream_id,
    process_order,
    create_schema_if_needed,
    sweep_orphan_customers,
)
from utils.api_client import fetch_stream_raw
from services.clustering_service import OrderItemCluster

class SyncStatus:
    def __init__(
        self,
        type,
        message=None,
        progress=0.0,
        current=0,
        total=0,
        stats=None,
        code=None,
    ):
        self.type = type # 'info', 'progress', 'done', 'error'
        self.message = message
        self.progress = progress
        self.current = current
        self.total = total
        self.stats = stats
        # Machine-readable failure classification. All Stores uses this to stop
        # after process-wide credential/configuration failures while continuing
        # after a restaurant-local failure.
        self.code = code

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
                yield SyncStatus(
                    'error',
                    f"Schema creation failed: {str(schema_error)}",
                    code=getattr(schema_error, "code", None),
                )
                return
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
        
        # Fetch orders. Named before the call: one page can take minutes when
        # the central server is slow, and the UI has nothing else to show.
        yield SyncStatus('info', "Fetching new orders from the POS stream...")
        new_orders, total_available = fetch_stream_raw(
            conn,
            endpoint="orders",
            start_cursor=start_cursor
        )

        # Validate the complete page before committing any order from it. A bad
        # row must not leave a partly mixed profile behind.
        from src.core.central_api import restaurant_id_from_connection

        requested_restaurant_id = restaurant_id_from_connection(conn)
        for order_payload in new_orders:
            raw_event = order_payload.get("raw_event") or {}
            raw_payload = raw_event.get("raw_payload") or {}
            properties = raw_payload.get("properties") or {}
            returned_restaurant_id = str(
                ((properties.get("Restaurant") or {}).get("restID")) or ""
            ).strip()
            if returned_restaurant_id and returned_restaurant_id != requested_restaurant_id:
                yield SyncStatus(
                    "error",
                    "Restaurant isolation failure: central order page returned "
                    f"{returned_restaurant_id} while syncing {requested_restaurant_id}",
                )
                return
        
        if not new_orders:
            # Orderless runs still owe the husk-free guarantee
            # (MENU_SYNC_ARCHITECTURE.md §2.4.1): husks stranded by earlier
            # runs may have aged past the grace window since the last sweep.
            cursor = conn.cursor()
            try:
                swept_customers = sweep_orphan_customers(cursor)
                conn.commit()
            finally:
                cursor.close()
            if swept_customers:
                yield SyncStatus('info', f"🧹 Swept {len(swept_customers)} orphan customer record(s).")
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
        
        # Derive menu_item_variants state from observed addon usage:
        #  1. seed mappings for addon-only pairs the ingest path never mapped,
        #  2. flag addon eligibility. Both are batched and idempotent.
        from utils.menu_item_variant_enforcement import (
            backfill_addon_only_variant_mappings,
            mark_addon_eligible_from_usage,
        )
        from src.core.itemcode_mapping import rebuild_itemcode_mappings_best_effort
        cursor = conn.cursor()
        try:
            seeded = backfill_addon_only_variant_mappings(conn, cursor=cursor)
            flagged = mark_addon_eligible_from_usage(conn, cursor=cursor)
            # All orders and addon backfills are in: refresh the derived
            # itemcode projection before the final commit.
            itemcode_rebuild = rebuild_itemcode_mappings_best_effort(conn, cursor=cursor)
            # State-scoped GC: order replay can re-point orders to a different
            # customer (identity-key drift), stranding the old owner — sweep
            # zero-reference customers past the grace window.
            swept_customers = sweep_orphan_customers(cursor)
            conn.commit()
        finally:
            cursor.close()
        if seeded:
            yield SyncStatus('info', f"🧩 Seeded {seeded} addon-only variant mapping(s).")
        if flagged:
            yield SyncStatus('info', f"🏷️ Marked {flagged} variant(s) addon-eligible from usage.")
        if itemcode_rebuild is not None:
            yield SyncStatus('info', f"🔗 Itemcode projection rebuilt: {itemcode_rebuild.summary()}")
        if swept_customers:
            yield SyncStatus('info', f"🧹 Swept {len(swept_customers)} orphan customer record(s).")
        yield SyncStatus('done', "Sync Complete", progress=1.0, stats=stats, total=total_orders)
        
    except Exception as e:
        yield SyncStatus(
            'error',
            f"Sync error: {str(e)}\n{traceback.format_exc()}",
            code=getattr(e, "code", None),
        )
