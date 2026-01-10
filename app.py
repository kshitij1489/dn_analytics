"""
Analytics Database Client Application

A Streamlit web application for querying and managing the analytics database.

Features:
- SQL query interface
- Incremental database sync
- Table browsing with pagination
- Real-time data visualization

Usage:
    streamlit run app.py
"""

import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
from datetime import datetime
import sys
from pathlib import Path
import os

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from database.load_orders import (
    create_postgresql_connection,
    get_last_stream_id,
    process_order,
    get_or_create_restaurant,
    get_or_create_customer,
    create_schema_if_needed
)
from database.menu_manager import sync_menu
from utils.api_client import fetch_stream_raw
from data_cleaning.item_matcher import ItemMatcher
from datetime import datetime

# Page configuration
st.set_page_config(
    page_title="Analytics Database Client",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'db_connected' not in st.session_state:
    st.session_state.db_connected = False
if 'db_conn' not in st.session_state:
    st.session_state.db_conn = None
if 'item_matcher' not in st.session_state:
    st.session_state.item_matcher = None


def get_db_connection():
    """Get or create database connection"""
    if st.session_state.db_conn is None or st.session_state.db_conn.closed:
        # Try to get connection from secrets or environment
        try:
            db_url = None
            # Try secrets first
            try:
                db_url = st.secrets.get("database", {}).get("url")
            except:
                pass
            
            # Try environment variable
            if not db_url:
                db_url = os.environ.get("DB_URL")
            
            # Default fallback
            if not db_url:
                db_url = "postgresql://kshitijsharma@localhost:5432/analytics"
            
            st.session_state.db_conn = psycopg2.connect(db_url)
            
            # Create schema if it doesn't exist
            try:
                create_schema_if_needed(st.session_state.db_conn)
            except Exception as schema_error:
                st.warning(f"Schema creation warning: {schema_error}")
            
            st.session_state.db_connected = True
        except Exception as e:
            st.error(f"Database connection failed: {e}")
            st.info("💡 Tip: Set up database connection in `.streamlit/secrets.toml` or use DB_URL environment variable")
            st.session_state.db_connected = False
            return None
    
    return st.session_state.db_conn


def execute_query(conn, query, limit=None):
    """Execute SQL query and return results as DataFrame"""
    try:
        if limit:
            query = f"{query.rstrip(';')} LIMIT {limit}"
        
        df = pd.read_sql_query(query, conn)
        return df, None
    except Exception as e:
        return None, str(e)


def sync_database(conn):
    """Sync database with incremental updates"""
    try:
        # Check if schema exists, create if needed
        cursor = conn.cursor()
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'orders'
            )
        """)
        tables_exist = cursor.fetchone()[0]
        cursor.close()
        
        if not tables_exist:
            # Create schema first
            try:
                create_schema_if_needed(conn)
                st.info("📋 Database schema created.")
            except Exception as schema_error:
                return 0, f"Schema creation failed: {str(schema_error)}"
        
        # 1. Sync Menu Data
        status_text = st.empty()
        status_text.text("Syncing menu data...")
        menu_result = sync_menu(conn)
        if menu_result['status'] == 'error':
            return 0, f"Menu sync failed: {menu_result['message']}"
        st.toast(f"Menu synced: {menu_result.get('menu_items', 0)} items")
        
        # Check if menu data is loaded (needed for ItemMatcher)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM menu_items")
        menu_count = cursor.fetchone()[0]
        cursor.close()
        
        if menu_count == 0:
            return 0, "Menu data failed to load."
        
        # Check if customers table is empty (indicates migration or first run)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customers")
        customer_count = cursor.fetchone()[0]
        cursor.close()
        
        # Determine if we need a full reload or incremental sync
        if customer_count == 0:
            # Full reload: customers table is empty (migration or first run)
            status_text.text("🔄 Customers table empty - performing full reload...")
            st.info("📥 Performing full reload of all orders (customers table is empty)")
            start_cursor = 0
        else:
            # Incremental sync: normal operation
            start_cursor = get_last_stream_id(conn) + 1
        
        # Fetch orders
        new_orders = fetch_stream_raw(
            endpoint="orders",
            start_cursor=start_cursor
        )
        
        if not new_orders:
            status_text.empty()
            return 0, "No new orders to sync"
        
        # Initialize ItemMatcher if needed
        if st.session_state.item_matcher is None:
            st.session_state.item_matcher = ItemMatcher(conn)
        
        stats = {
            'orders': 0,
            'order_items': 0,
            'order_item_addons': 0,
            'order_taxes': 0,
            'order_discounts': 0,
            'errors': []
        }
        
        progress_bar = st.progress(0)
        
        for i, order_payload in enumerate(new_orders):
            status_text.text(f"Processing order {i+1}/{len(new_orders)}...")
            progress_bar.progress((i + 1) / len(new_orders))
            
            order_stats = process_order(conn, order_payload, st.session_state.item_matcher)
            for key in stats:
                if key == 'errors':
                    stats[key].extend(order_stats[key])
                else:
                    stats[key] += order_stats[key]
        
        progress_bar.empty()
        status_text.empty()
        
        return len(new_orders), stats
    except Exception as e:
        import traceback
        return 0, f"Sync error: {str(e)}\n{traceback.format_exc()}"


def get_table_data(conn, table_name, page=1, page_size=50, sort_column=None, sort_direction='DESC'):
    """Get paginated table data"""
    try:
        # Determine sort column based on table
        if sort_column is None:
            if table_name == 'orders':
                sort_column = 'created_on'
            elif table_name == 'order_items':
                sort_column = 'created_at'
            elif table_name == 'customers':
                sort_column = 'last_order_date'
            elif table_name == 'menu_items':
                sort_column = 'type, name'
                sort_direction = 'ASC'
            elif table_name == 'variants':
                sort_column = 'variant_name'
                sort_direction = 'ASC'
            else:
                sort_column = 'created_at'
        
        # Get total count
        count_query = f"SELECT COUNT(*) as count FROM {table_name}"
        total_count = pd.read_sql_query(count_query, conn).iloc[0]['count']
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Build query
        query = f"""
            SELECT * FROM {table_name}
            ORDER BY {sort_column} {sort_direction}
            LIMIT {page_size} OFFSET {offset}
        """
        
        df = pd.read_sql_query(query, conn)
        
        return df, total_count, None
    except Exception as e:
        return None, 0, str(e)


# Main App
st.title("📊 Analytics Database Client")
st.markdown("---")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuration")
    
    # Database connection
    if st.button("🔌 Connect to Database"):
        conn = get_db_connection()
        if conn:
            st.success("✅ Connected to database")
            
            # Check if schema exists
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = 'orders'
                    )
                """)
                tables_exist = cursor.fetchone()[0]
                cursor.close()
                
                if not tables_exist:
                    st.info("📋 Schema will be created automatically when needed")
                else:
                    st.success("✅ Database ready")
            except Exception as e:
                st.warning(f"Schema check: {e}")
    
    if st.session_state.db_connected:
        st.success("🟢 Database Connected")
        
        # Database setup
        st.markdown("---")
        st.header("🛠️ Database Setup")
        
        # Check if schema exists
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = 'orders'
                    )
                """)
                tables_exist = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM menu_items")
                menu_count = cursor.fetchone()[0] if tables_exist else 0
                cursor.close()
                
                if not tables_exist:
                    st.warning("⚠️ Database schema not initialized")
                    if st.button("📋 Create Schema", use_container_width=True):
                        with st.spinner("Creating schema..."):
                            try:
                                create_schema_if_needed(conn)
                                st.success("✅ Schema created successfully!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Schema creation failed: {e}")
                elif menu_count == 0:
                    st.warning("⚠️ Menu data not loaded")
                    if st.button("📥 Load Menu", use_container_width=True):
                        with st.spinner("Loading menu..."):
                            res = sync_menu(conn)
                            if res['status'] == 'success':
                                st.success(f"✅ Loaded {res['menu_items']} items")
                                st.rerun()
                            else:
                                st.error(f"Failed: {res['message']}")
                else:
                    st.success("✅ Database ready")
        except Exception as e:
            st.error(f"Setup check failed: {e}")
        
        # Sync button
        st.markdown("---")
        st.header("🔄 Sync Database")
        if st.button("Sync New Orders", type="primary", use_container_width=True):
            conn = get_db_connection()
            if conn:
                with st.spinner("Syncing..."):
                    count, result = sync_database(conn)
                    if isinstance(result, dict):
                        st.success(f"✅ Synced {count} new orders")
                        st.json(result)
                    else:
                        if "Schema created" in result or "Menu data not loaded" in result:
                            st.warning(result)
                        else:
                            st.info(result)
    else:
        st.warning("🔴 Not Connected")
        st.info("Click 'Connect to Database' to establish connection")
    
    st.markdown("---")
    
    # Database info
    if st.session_state.db_connected:
        st.header("📈 Database Stats")
        try:
            conn = get_db_connection()
            if conn:
                # Check if tables exist first
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = 'orders'
                    )
                """)
                tables_exist = cursor.fetchone()[0]
                cursor.close()
                
                if tables_exist:
                    stats_query = """
                        SELECT 
                            (SELECT COUNT(*) FROM orders) as orders,
                            (SELECT COUNT(*) FROM order_items) as order_items,
                            (SELECT COUNT(*) FROM customers) as customers,
                            (SELECT MAX(created_on) FROM orders) as last_order
                    """
                    stats = pd.read_sql_query(stats_query, conn).iloc[0]
                    st.metric("Orders", f"{stats['orders']:,}")
                    st.metric("Order Items", f"{stats['order_items']:,}")
                    st.metric("Customers", f"{stats['customers']:,}")
                    if stats['last_order']:
                        st.caption(f"Last Order: {stats['last_order']}")
                else:
                    st.info("📋 Database schema not initialized")
        except Exception as e:
            st.warning(f"Stats unavailable: {e}")

# Main content area
if not st.session_state.db_connected:
    st.info("👈 Please connect to the database using the sidebar")
    st.stop()

conn = get_db_connection()
if not conn:
    st.error("Failed to establish database connection")
    st.stop()

# Tabs for different views
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs([
    "🔍 SQL Query",
    "📦 Orders",
    "🛒 Order Items",
    "👥 Customers",
    "🍽️ Restaurants",
    "📋 Menu Items",
    "📏 Variants",
    "🕸️ Menu Matrix",
    "📊 Taxes",
    "💰 Discounts"
])

# Tab 1: SQL Query
with tab1:
    st.header("SQL Query Interface")
    
    # Query input
    query = st.text_area(
        "Enter SQL Query:",
        height=150,
        placeholder="SELECT * FROM orders LIMIT 10;"
    )
    
    col1, col2 = st.columns([1, 4])
    with col1:
        limit = st.number_input("Limit Results", min_value=1, max_value=10000, value=1000, step=100)
    with col2:
        execute = st.button("▶️ Execute Query", type="primary", use_container_width=True)
    
    if execute and query:
        with st.spinner("Executing query..."):
            df, error = execute_query(conn, query, limit=limit if limit < 10000 else None)
            
            if error:
                st.error(f"Query Error: {error}")
            else:
                st.success(f"✅ Query executed successfully. Returned {len(df)} rows.")
                
                # Display results
                st.dataframe(df, use_container_width=True, height=400)
                
                # Download button
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download as CSV",
                    data=csv,
                    file_name=f"query_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )

# Tab 2: Orders
with tab2:
    st.header("Orders Table")
    
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        page_size = st.selectbox("Rows per page", [25, 50, 100, 200], index=1)
    with col2:
        sort_direction = st.selectbox("Sort", ["Newest First", "Oldest First"], index=0)
    with col3:
        refresh = st.button("🔄 Refresh")
    
    # Pagination
    if 'orders_page' not in st.session_state:
        st.session_state.orders_page = 1
    
    if refresh:
        st.session_state.orders_page = 1
    
    df, total_count, error = get_table_data(
        conn,
        'orders',
        page=st.session_state.orders_page,
        page_size=page_size,
        sort_direction='DESC' if sort_direction == "Newest First" else 'ASC'
    )
    
    if error:
        st.error(f"Error: {error}")
    else:
        total_pages = (total_count + page_size - 1) // page_size
        
        st.info(f"Showing page {st.session_state.orders_page} of {total_pages} (Total: {total_count:,} orders)")
        
        # Pagination logic
        def set_page(page):
            st.session_state.orders_page = page
            # We don't need to manually set orders_page_input if we rely on the rerender to pick up new value?
            # Actually, to update a widget with key, we MUST update the key in session state.
            st.session_state.orders_page_input = page

        def sync_input():
            st.session_state.orders_page = st.session_state.orders_page_input

        # Pagination controls
        col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])
        with col1:
            st.button(
                "⏮️ First", 
                key="orders_first", 
                disabled=st.session_state.orders_page == 1,
                on_click=set_page,
                args=(1,)
            )
        with col2:
            st.button(
                "◀️ Previous", 
                key="orders_prev", 
                disabled=st.session_state.orders_page == 1,
                on_click=set_page,
                args=(st.session_state.orders_page - 1,)
            )
        with col3:
            if total_pages > 0:
                # Ensure input key exists
                if "orders_page_input" not in st.session_state:
                    st.session_state.orders_page_input = st.session_state.orders_page
                
                st.number_input(
                    "Page",
                    min_value=1,
                    max_value=total_pages,
                    key="orders_page_input",
                    on_change=sync_input
                )
            else:
                st.info("No data available.")
                st.stop()
                
        with col4:
            st.button(
                "Next ▶️", 
                key="orders_next", 
                disabled=st.session_state.orders_page >= total_pages,
                on_click=set_page,
                args=(st.session_state.orders_page + 1,)
            )
        with col5:
            st.button(
                "Last ⏭️", 
                key="orders_last", 
                disabled=st.session_state.orders_page >= total_pages,
                on_click=set_page,
                args=(total_pages,)
            )
        st.dataframe(df, use_container_width=True, height=500)

# Tab 3: Order Items
with tab3:
    st.header("Order Items Table")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        page_size = st.selectbox("Rows per page", [25, 50, 100, 200], index=1, key="items_page_size")
    with col2:
        refresh = st.button("🔄 Refresh", key="items_refresh")
    
    if 'order_items_page' not in st.session_state:
        st.session_state.order_items_page = 1
    
    if refresh:
        st.session_state.order_items_page = 1
    
    df, total_count, error = get_table_data(
        conn,
        'order_items',
        page=st.session_state.order_items_page,
        page_size=page_size
    )
    
    if error:
        st.error(f"Error: {error}")
    else:
        total_pages = (total_count + page_size - 1) // page_size
        st.info(f"Page {st.session_state.order_items_page} of {total_pages} (Total: {total_count:,} items)")
        
        # Simple pagination
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if st.button("◀️ Prev", key="items_prev", disabled=st.session_state.order_items_page == 1):
                st.session_state.order_items_page -= 1
                st.rerun()
        with col2:
            st.write(f"Page {st.session_state.order_items_page}/{total_pages}")
        with col3:
            if st.button("Next ▶️", key="items_next", disabled=st.session_state.order_items_page >= total_pages):
                st.session_state.order_items_page += 1
                st.rerun()
        
        st.dataframe(df, use_container_width=True, height=500)

# Tab 4: Customers
with tab4:
    st.header("Customers Table")
    
    page_size = st.selectbox("Rows per page", [25, 50, 100], index=1, key="customers_page_size")
    
    if 'customers_page' not in st.session_state:
        st.session_state.customers_page = 1
    
    df, total_count, error = get_table_data(
        conn,
        'customers',
        page=st.session_state.customers_page,
        page_size=page_size
    )
    
    if error:
        st.error(f"Error: {error}")
    else:
        total_pages = (total_count + page_size - 1) // page_size
        st.info(f"Page {st.session_state.customers_page} of {total_pages} (Total: {total_count:,} customers)")
        
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if st.button("◀️ Prev", key="cust_prev", disabled=st.session_state.customers_page == 1):
                st.session_state.customers_page -= 1
                st.rerun()
        with col2:
            st.write(f"Page {st.session_state.customers_page}/{total_pages}")
        with col3:
            if st.button("Next ▶️", key="cust_next", disabled=st.session_state.customers_page >= total_pages):
                st.session_state.customers_page += 1
                st.rerun()
        
        st.dataframe(df, use_container_width=True, height=500)

# Tab 5: Restaurants
with tab5:
    st.header("Restaurants Table")
    
    df, total_count, error = get_table_data(conn, 'restaurants', page=1, page_size=100)
    
    if error:
        st.error(f"Error: {error}")
    else:
        st.info(f"Total: {total_count} restaurants")
        st.dataframe(df, use_container_width=True)

# Tab 6: Menu Items
with tab6:
    st.header("Menu Items")
    
    df, total_count, error = get_table_data(conn, 'menu_items', page=1, page_size=1000)
    
    if error:
        st.error(f"Error: {error}")
    else:
        st.info(f"Total: {total_count} items")
        st.dataframe(df, use_container_width=True, height=600)

# Tab 7: Variants
with tab7:
    st.header("Variants")
    
    df, total_count, error = get_table_data(conn, 'variants', page=1, page_size=1000)
    
    if error:
        st.error(f"Error: {error}")
    else:
        st.info(f"Total: {total_count} variants")
        st.dataframe(df, use_container_width=True)

# Tab 8: Menu Matrix
with tab8:
    st.header("Menu Matrix (Item x Variant)")
    
    query = """
        SELECT 
            mi.name, 
            mi.type, 
            v.variant_name, 
            miv.price, 
            miv.is_active, 
            miv.addon_eligible, 
            miv.delivery_eligible 
        FROM menu_item_variants miv
        JOIN menu_items mi ON miv.menu_item_id = mi.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        ORDER BY mi.type, mi.name, v.variant_name
    """
    
    with st.spinner("Loading matrix..."):
        df, error = execute_query(conn, query)
        
        if error:
            st.error(f"Error: {error}")
        else:
            st.info(f"Total combinations: {len(df)}")
            st.dataframe(df, use_container_width=True, height=600)

# Tab 9: Order Taxes
with tab9:
    st.header("Order Taxes Table")
    
    page_size = st.selectbox("Rows per page", [25, 50, 100], index=1, key="taxes_page_size")
    
    if 'taxes_page' not in st.session_state:
        st.session_state.taxes_page = 1
    
    df, total_count, error = get_table_data(
        conn,
        'order_taxes',
        page=st.session_state.taxes_page,
        page_size=page_size
    )
    
    if error:
        st.error(f"Error: {error}")
    else:
        total_pages = (total_count + page_size - 1) // page_size
        st.info(f"Page {st.session_state.taxes_page} of {total_pages} (Total: {total_count:,} tax records)")
        
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if st.button("◀️ Prev", key="tax_prev", disabled=st.session_state.taxes_page == 1):
                st.session_state.taxes_page -= 1
                st.rerun()
        with col2:
            st.write(f"Page {st.session_state.taxes_page}/{total_pages}")
        with col3:
            if st.button("Next ▶️", key="tax_next", disabled=st.session_state.taxes_page >= total_pages):
                st.session_state.taxes_page += 1
                st.rerun()
        
        st.dataframe(df, use_container_width=True, height=500)

# Tab 10: Order Discounts
with tab10:
    st.header("Order Discounts Table")
    
    page_size = st.selectbox("Rows per page", [25, 50, 100], index=1, key="discounts_page_size")
    
    if 'discounts_page' not in st.session_state:
        st.session_state.discounts_page = 1
    
    df, total_count, error = get_table_data(
        conn,
        'order_discounts',
        page=st.session_state.discounts_page,
        page_size=page_size
    )
    
    if error:
        st.error(f"Error: {error}")
    else:
        total_pages = (total_count + page_size - 1) // page_size
        st.info(f"Page {st.session_state.discounts_page} of {total_pages} (Total: {total_count:,} discount records)")
        
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if st.button("◀️ Prev", key="disc_prev", disabled=st.session_state.discounts_page == 1):
                st.session_state.discounts_page -= 1
                st.rerun()
        with col2:
            st.write(f"Page {st.session_state.discounts_page}/{total_pages}")
        with col3:
            if st.button("Next ▶️", key="disc_next", disabled=st.session_state.discounts_page >= total_pages):
                st.session_state.discounts_page += 1
                st.rerun()
        
        st.dataframe(df, use_container_width=True, height=500)

# Footer
st.markdown("---")
st.caption("Analytics Database Client | Built with Streamlit")
