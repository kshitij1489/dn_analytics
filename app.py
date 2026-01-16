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
from psycopg2.extras import RealDictCursor, execute_values
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
from utils.api_client import fetch_stream_raw
from services.clustering_service import OrderItemCluster
from utils.menu_utils import merge_menu_items, resolve_item_rename, remap_order_item_cluster, undo_merge
from scripts.seed_from_backups import perform_seeding, export_to_backups
from scripts.resolve_unclustered import get_unverified_items, verify_item

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
if 'item_cluster' not in st.session_state:
    st.session_state.item_cluster = None
if 'data_version' not in st.session_state:
    st.session_state.data_version = 0
if 'initialized' not in st.session_state:
    st.session_state.initialized = False

# --- AUTOMATIC INITIALIZATION ---
def initialize_app():
    """Initialize database connection and services automatically"""
    if st.session_state.initialized:
        return
        
    conn = get_db_connection()
    if conn:
        # 0. Ensure schema is ready
        try:
            create_schema_if_needed(conn)
        except Exception as e:
            st.warning(f"Schema check error: {e}")
            
        # 1. Auto-seed if empty
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM menu_items")
            count = cursor.fetchone()[0]
            cursor.close()
            
            if count == 0:
                st.info("🌱 Clean database detected. Seeding from backups...")
                if perform_seeding(conn):
                    st.success("✅ Seed data loaded successfully")
                else:
                    st.warning("⚠️ Could not load seed data")
        except Exception as e:
            st.error(f"Seeding check failed: {e}")
            
        # 2. Initialize cluster
        if st.session_state.item_cluster is None:
            st.session_state.item_cluster = OrderItemCluster(conn)
            
        st.session_state.initialized = True



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
            st.session_state.db_connected = True
        except Exception as e:
            st.error(f"Database connection failed: {e}")
            st.info("💡 Tip: Set up database connection in `.streamlit/secrets.toml` or use DB_URL environment variable")
            st.session_state.db_connected = False
            return None

    
    return st.session_state.db_conn


initialize_app()


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
        
        
        # 1. Prepare for Sync
        status_text = st.empty()
        
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
        
        # Initialize OrderItemCluster if needed
        if st.session_state.item_cluster is None:
            st.session_state.item_cluster = OrderItemCluster(conn)
        
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
            
            order_stats = process_order(conn, order_payload, st.session_state.item_cluster)
            for key in stats:
                if key == 'errors':
                    stats[key].extend(order_stats[key])
                else:
                    stats[key] += order_stats[key]
        
        progress_bar.empty()
        status_text.empty()
        
        # Increment version to trigger automatic UI refresh across all tabs
        if len(new_orders) > 0:
            st.session_state.data_version += 1
            # Export to backups
            export_to_backups(conn)
            
        return len(new_orders), stats
    except Exception as e:
        import traceback
        return 0, f"Sync error: {str(e)}\n{traceback.format_exc()}"

def get_table_data(conn, table_name, page=1, page_size=50, sort_column=None, sort_direction='DESC', filters=None):
    """Get paginated table data with optional multi-column filtering"""
    try:
        # Determine sort column based on table if not provided
        if sort_column is None:
            if table_name == 'orders':
                sort_column = 'created_on'
            elif table_name == 'order_items':
                sort_column = 'created_at'
            elif table_name == 'customers':
                sort_column = 'last_order_date'
            elif table_name == 'menu_items':
                sort_column = 'name'
                sort_direction = 'ASC'
            elif table_name == 'variants':
                sort_column = 'variant_name'
                sort_direction = 'ASC'
            else:
                sort_column = 'created_at'
        
        # Build WHERE clause from filters
        where_clause = ""
        params = []
        if filters:
            conditions = []
            for col, val in filters.items():
                if val:
                    # Use CAST to TEXT to allow searching across IDs and numbers
                    conditions.append(f"CAST({col} AS TEXT) ILIKE %s")
                    params.append(f"%{val}%")
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)
        
        # Get total count with filters
        count_query = f"SELECT COUNT(*) as count FROM {table_name} {where_clause}"
        cursor = conn.cursor()
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        cursor.close()
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Build query
        query = f"""
            SELECT * FROM {table_name}
            {where_clause}
            ORDER BY {sort_column} {sort_direction}
            LIMIT {page_size} OFFSET {offset}
        """
        
        # execute_query uses pd.read_sql_query which doesn't easily take params for the WHERE clause
        # so we'll use a cursor and then create the DF
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query, params)
        df = pd.DataFrame(cursor.fetchall())
        cursor.close()
        
        return df, total_count, None
    except Exception as e:
        return None, 0, str(e)


def render_datatable(conn, table_name, default_sort_col, sort_columns, page_key_prefix, search_columns=None):
    """Reusable component for rendering a paginated, globally sortable table with per-column filtering"""
    
    # 1. Search Filters
    filters = {}
    if search_columns:
        st.write("🔍 **Quick Filters**")
        # Split search columns into rows of up to 4
        n_cols = len(search_columns)
        rows = [search_columns[i:i + 4] for i in range(0, n_cols, 4)]
        
        for row in rows:
            cols = st.columns(len(row))
            for i, col_name in enumerate(row):
                with cols[i]:
                    filter_val = st.text_input(
                        f"Search {col_name}", 
                        key=f"{page_key_prefix}_filter_{col_name}",
                        placeholder=f"Filter {col_name}..."
                    )
                    if filter_val:
                        filters[col_name] = filter_val

    # 2. UI Controls
    col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
    
    # Define reset callback before rendering buttons
    def reset_filters():
        for col_name in (search_columns or []):
            st.session_state[f"{page_key_prefix}_filter_{col_name}"] = ""
        st.session_state[f"{page_key_prefix}_page"] = 1

    with col1:
        # Find index of default sort column
        try:
            default_index = sort_columns.index(default_sort_col)
        except ValueError:
            default_index = 0
            
        sort_col = st.selectbox(
            f"Sort {table_name} by", 
            sort_columns, 
            index=default_index, 
            key=f"{page_key_prefix}_sort_col"
        )
    with col2:
        sort_dir = st.selectbox(
            "Direction", 
            ["Descending", "Ascending"], 
            index=0, 
            key=f"{page_key_prefix}_sort_dir"
        )
    with col3:
        page_size = st.selectbox(
            "Rows/page", 
            [25, 50, 100, 200], 
            index=1, 
            key=f"{page_key_prefix}_page_size"
        )
    with col4:
        st.write("") # Spacer
        st.button("🧹", key=f"{page_key_prefix}_clear", help="Clear Filters", on_click=reset_filters, use_container_width=True)

    # 3. Pagination State
    page_state_key = f"{page_key_prefix}_page"
    if page_state_key not in st.session_state:
        st.session_state[page_state_key] = 1

    # Reset page if filter, page size, or global data version changes
    filter_state_keys = [f"{page_key_prefix}_filter_{c}" for c in (search_columns or [])]
    current_state = {k: st.session_state.get(k, "") for k in filter_state_keys}
    current_state["page_size"] = page_size
    current_state["data_version"] = st.session_state.data_version
    
    state_record_key = f"{page_key_prefix}_last_state"
    if state_record_key not in st.session_state:
        st.session_state[state_record_key] = current_state
    elif st.session_state[state_record_key] != current_state:
        st.session_state[page_state_key] = 1
        st.session_state[state_record_key] = current_state

    # 4. Data Fetching
    df, total_count, error = get_table_data(
        conn,
        table_name,
        page=st.session_state[page_state_key],
        page_size=page_size,
        sort_column=sort_col,
        sort_direction='DESC' if sort_dir == "Descending" else 'ASC',
        filters=filters
    )

    if error:
        st.error(f"Error loading {table_name}: {error}")
        return

    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
    if total_count == 0:
        st.info(f"No records found matching filters.")
        return

    # 5. Pagination Controls
    st.info(f"Showing page {st.session_state[page_state_key]} of {total_pages} (Total: {total_count:,} records)")
    
    def set_page(p):
        st.session_state[page_state_key] = p

    p_col1, p_col2, p_col3, p_col4, p_col5 = st.columns([1, 1, 2, 1, 1])
    with p_col1:
        st.button("⏮️", key=f"{page_key_prefix}_first", disabled=st.session_state[page_state_key] == 1, on_click=set_page, args=(1,))
    with p_col2:
        st.button("◀️", key=f"{page_key_prefix}_prev", disabled=st.session_state[page_state_key] == 1, on_click=set_page, args=(st.session_state[page_state_key] - 1,))
    with p_col3:
        new_page = st.number_input(
            "Go to page", 
            min_value=1, 
            max_value=total_pages if total_pages > 0 else 1, 
            value=st.session_state[page_state_key],
            key=f"{page_key_prefix}_page_input_widget"
        )
        if new_page != st.session_state[page_state_key]:
            st.session_state[page_state_key] = new_page
            st.rerun()
    with p_col4:
        st.button("▶️", key=f"{page_key_prefix}_next", disabled=st.session_state[page_state_key] >= total_pages, on_click=set_page, args=(st.session_state[page_state_key] + 1,))
    with p_col5:
        st.button("⏭️", key=f"{page_key_prefix}_last", disabled=st.session_state[page_state_key] >= total_pages, on_click=set_page, args=(total_pages,))

    # 6. Display Table
    cols = df.columns.tolist()
    
    # Define analytics columns to move for better visibility
    to_move = ['total_revenue', 'total_sold', 'sold_as_item', 'sold_as_addon', 'total_spent', 'total_orders']
    
    # Find anchor point to insert after
    anchor = None
    for a in ['is_active', 'name', 'order_id']:
        if a in cols:
            anchor = a
            break
            
    if anchor:
        for c in to_move:
            if c in cols:
                cols.remove(c)
                anchor_idx = cols.index(anchor)
                cols.insert(anchor_idx + 1, c)
                # Note: we don't increment anchor_idx because we want multiple 
                # analytics cols to cluster together after the anchor
            
    st.dataframe(df[cols], use_container_width=True, height=500)


# Main App
st.title("📊 Analytics Database Client")
st.markdown("---")

# Sidebar
with st.sidebar:
    st.header("⚙️ Configuration")
    
    if st.session_state.db_connected:
        st.success("✅ Connected to Database")
    else:
        st.error("❌ Database Not Connected")
        if st.button("🔄 Retry Connection"):
            get_db_connection()
            st.rerun()
    
    if st.session_state.db_connected:
        # Schema status check
        try:
            conn = st.session_state.db_conn
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
                cursor.close()
                
                if not tables_exist:
                    st.warning("⚠️ Schema Missing")
                else:
                    st.info("📊 Database Ready")
        except Exception as e:
            st.error(f"Setup check failed: {e}")
        
        # Sync button
        st.markdown("---")
        st.header("🔄 Sync Database")
        if st.button("Sync New Orders", type="primary", width="stretch"):
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
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11 = st.tabs([
    "🔍 SQL Query",
    "✨ Resolutions",
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

# Tab 2: Resolutions (Unclustered Data)
with tab2:
    st.header("✨ Unclustered Data Resolution")
    st.info("Resolve items that don't match existing menu items exactly.")
    
    unverified_items = get_unverified_items(conn)
    
    if not unverified_items:
        st.success("✅ All items are verified! No conflicts found.")
    else:
        st.write(f"Found {len(unverified_items)} unverified items.")
        
        # Group by item for cleaner UI
        for i, item in enumerate(unverified_items):
            with st.expander(f"📝 {item['name']} ({item['type']})", expanded=(i==0)):
                col1, col2 = st.columns(2)
                
                # Fetch Suggestion if any
                suggestion_name = None
                if item.get('suggestion_id'):
                    cursor = conn.cursor()
                    cursor.execute("SELECT name, type FROM menu_items WHERE menu_item_id = %s", (item['suggestion_id'],))
                    s_res = cursor.fetchone()
                    cursor.close()
                    if s_res:
                        suggestion_name = f"{s_res[0]} ({s_res[1]})"
                
                with col1:
                    st.write("**Current Details:**")
                    st.write(f"- Name: `{item['name']}`")
                    st.write(f"- Type: `{item['type']}`")
                    st.write(f"- Created: {item['created_at']}")
                    if suggestion_name:
                        st.info(f"💡 Suggestion: **{suggestion_name}**")
                
                with col2:
                    st.write("**Action:**")
                    action = st.radio("Choose resolution:", 
                                    ["Merge into Existing", "Rename / Create New", "Verify as is"], 
                                    key=f"act_{item['menu_item_id']}", index=0 if suggestion_name else 2)
                    
                    if action == "Merge into Existing":
                        # Fetch all verified items for dropdown
                        # (Optimized: ideally cache this or use autocomplete)
                        cursor = conn.cursor()
                        cursor.execute("SELECT menu_item_id, name, type FROM menu_items WHERE is_verified = TRUE ORDER BY name")
                        verified_options = cursor.fetchall()
                        cursor.close()
                        
                        # Find index of suggestion if available
                        default_idx = 0
                        if item.get('suggestion_id'):
                            for idx, v_opt in enumerate(verified_options):
                                if str(v_opt[0]) == str(item['suggestion_id']):
                                    default_idx = idx
                                    break
                                    
                        target_choice = st.selectbox("Select Target Item:", 
                                                   options=verified_options,
                                                   format_func=lambda x: f"{x[1]} ({x[2]})",
                                                   index=default_idx,
                                                   key=f"target_{item['menu_item_id']}")
                                                   
                        if st.button("Merge", key=f"btn_merge_{item['menu_item_id']}"):
                            res = merge_menu_items(conn, item['menu_item_id'], str(target_choice[0]))
                            if res['status'] == 'success':
                                st.success(res['message'])
                                st.rerun()
                            else:
                                st.error(res['message'])
                                
                    elif action == "Rename / Create New":
                        new_name = st.text_input("New Name", value=item['name'], key=f"name_{item['menu_item_id']}")
                        new_type = st.text_input("New Type", value=item['type'], key=f"type_{item['menu_item_id']}")
                        
                        if st.button("Save & Verify", key=f"btn_save_{item['menu_item_id']}"):
                            res = resolve_item_rename(conn, item['menu_item_id'], new_name, new_type)
                            if res['status'] == 'success':
                                st.success(res['message'])
                                st.rerun()
                            else:
                                st.error(res['message'])
                                
                    elif action == "Verify as is":
                        if st.button("Confirm Verify", key=f"btn_verify_{item['menu_item_id']}"):
                            verify_item(conn, item['menu_item_id'])
                            st.success("Item verified")
                            st.rerun()


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
        execute = st.button("▶️ Execute Query", type="primary", width="stretch")
    
    if execute and query:
        with st.spinner("Executing query..."):
            df, error = execute_query(conn, query, limit=limit if limit < 10000 else None)
            
            if error:
                st.error(f"Query Error: {error}")
            else:
                st.success(f"✅ Query executed successfully. Returned {len(df)} rows.")
                
                # Display results
                st.dataframe(df, width="stretch", height=400)
                
                # Download button
                csv = df.to_csv(index=False)
                st.download_button(
                    label="📥 Download as CSV",
                    data=csv,
                    file_name=f"query_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )

# Tab 3: Orders
with tab3:
    st.header("Orders Table")
    render_datatable(
        conn, 
        'orders', 
        'created_on', 
        ['order_id', 'created_on', 'total', 'order_type', 'order_from', 'order_status', 'petpooja_order_id'],
        'orders',
        search_columns=['order_id', 'order_status', 'order_type', 'order_from']
    )

# Tab 4: Order Items
with tab4:
    st.header("Order Items Table")
    render_datatable(
        conn, 
        'order_items', 
        'created_at', 
        ['order_item_id', 'order_id', 'created_at', 'total_price', 'quantity', 'name_raw', 'category_name', 'match_confidence'],
        'order_items',
        search_columns=['order_id', 'name_raw', 'category_name']
    )

# Tab 5: Customers
with tab5:
    st.header("Customers Table")
    render_datatable(
        conn, 
        'customers', 
        'last_order_date', 
        ['last_order_date', 'total_orders', 'total_spent', 'name', 'phone', 'first_order_date'],
        'customers',
        search_columns=['name', 'phone']
    )

# Tab 6: Restaurants
with tab6:
    st.header("Restaurants Table")
    render_datatable(
        conn, 
        'restaurants', 
        'restaurant_id', 
        ['restaurant_id', 'name', 'petpooja_restid'],
        'restaurants'
    )

# Tab 7: Menu Items
with tab7:
    # Merge Tool
    with st.expander("🛠️ Merge Menu Items", expanded=False):
        st.info("Merge a duplicate item (Source) into a canonical item (Target). The Source item will be DELETED and its stats/orders transferred to Target.")
        
        # Get list for Selectbox instead of raw ID input
        cursor = conn.cursor()
        cursor.execute("SELECT menu_item_id, name, type FROM menu_items ORDER BY name")
        all_items = cursor.fetchall()
        cursor.close()
        
        # Add placeholder
        placeholder = (None, "-- Select Item --", "")
        merge_options = [placeholder] + all_items
        
        c1, c2 = st.columns(2)
        with c1:
            src_choice = st.selectbox("Source (To Delete)", 
                                    options=merge_options, 
                                    format_func=lambda x: f"{x[1]} ({x[2]})" if x[0] else x[1],
                                    key="merge_src_sel")
        with c2:
            # Filter all_items to exclude source if source is selected
            target_options = [placeholder] + [i for i in all_items if not src_choice[0] or i[0] != src_choice[0]]
            tgt_choice = st.selectbox("Target (To Keep)", 
                                    options=target_options, 
                                    format_func=lambda x: f"{x[1]} ({x[2]})" if x[0] else x[1],
                                    key="merge_tgt_sel")
            
        if st.button("Merge Items"):
            if not src_choice[0] or not tgt_choice[0]:
                st.error("Please select both a Source and a Target item")
            elif src_choice[0] == tgt_choice[0]:
                st.error("Cannot merge item into itself")
            else:
                res = merge_menu_items(conn, str(src_choice[0]), str(tgt_choice[0]))
                if res['status'] == 'success':
                    st.success(res['message'])
                    st.write(res['stats'])
                    st.rerun()
                else:
                    st.error(res['message'])

    # Merge History
    with st.expander("⏳ Recent Merge History"):
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT h.*, m.name as target_name 
            FROM merge_history h
            LEFT JOIN menu_items m ON h.target_id = m.menu_item_id
            ORDER BY h.merged_at DESC 
            LIMIT 5
        """)
        history = cursor.fetchall()
        cursor.close()
        
        if not history:
            st.info("No recent merges found.")
        else:
            for record in history:
                col1, col2, col3 = st.columns([3, 3, 1])
                with col1:
                    target_display = record['target_name'] or f"Item {str(record['target_id'])[:8]}..."
                    st.write(f"**{record['source_name']}** → **{target_display}**")
                with col2:
                    st.caption(f"Relinked {len(record['affected_order_items'])} mappings • {record['merged_at'].strftime('%Y-%m-%d %H:%M')}")
                with col3:
                    if st.button("Undo", key=f"undo_{record['merge_id']}", use_container_width=True):
                        undo_res = undo_merge(conn, record['merge_id'])
                        if undo_res['status'] == 'success':
                            st.success(undo_res['message'])
                            st.rerun()
                        else:
                            st.error(undo_res['message'])

    render_datatable(
        conn, 
        'menu_items_summary_view', 
        'total_revenue', 
        ['total_revenue', 'total_sold', 'sold_as_item', 'sold_as_addon', 'name', 'type', 'is_active', 'menu_item_id'],
        'menu_items',
        search_columns=['menu_item_id', 'name', 'type', 'is_active']
    )

# Tab 8: Variants
with tab8:
    st.header("Variants")
    render_datatable(
        conn, 
        'variants', 
        'variant_name', 
        ['variant_id', 'variant_name'],
        'variants'
    )

# Tab 9: Menu Matrix
with tab9:
    st.header("Menu Matrix (Item x Variant)")
    
    
    # Remap Tool
    with st.expander("🔄 Remap Order Item"):
        # 1. Input Order Item ID
        raw_oid = st.text_input("Order Item ID (to move):")
        
        if raw_oid:
            # 2. Show current mapping
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.name, v.variant_name 
                FROM menu_item_variants mv
                JOIN menu_items m ON mv.menu_item_id = m.menu_item_id
                JOIN variants v ON mv.variant_id = v.variant_id
                WHERE mv.order_item_id = %s
            """, (raw_oid,))
            current = cursor.fetchone()
            cursor.close()
            
            if current:
                st.write(f"Currently mapped to: **{current[0]}** ({current[1]})")
                
                # 3. Select New Menu Item
                cursor = conn.cursor()
                cursor.execute("SELECT menu_item_id, name, type FROM menu_items ORDER BY name")
                all_items_remap = cursor.fetchall()
                
                # Select Item First
                new_item_choice = st.selectbox("Move to Menu Item:", 
                                            options=all_items_remap,
                                            format_func=lambda x: f"{x[1]} ({x[2]})",
                                            key="remap_item_sel")
                                            
                if new_item_choice:
                    cursor.execute("SELECT variant_id, variant_name FROM variants ORDER BY variant_name")
                    all_variants = cursor.fetchall()
                    cursor.close()
                    
                    new_variant_choice = st.selectbox("Select Variant:", 
                                                    options=all_variants, 
                                                    format_func=lambda x: x[1],
                                                    key="remap_var_sel")
                                                    
                    if st.button("Remap"):
                        res = remap_order_item_cluster(conn, raw_oid, str(new_item_choice[0]), str(new_variant_choice[0]))
                        if res['status'] == 'success':
                            st.success(res['message'])
                        else:
                            st.error(res['message'])
            else:
                st.warning("Order Item ID not found in cluster map")
    
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
            st.dataframe(df, width="stretch", height=600)

# Tab 10: Order Taxes
with tab10:
    st.header("Order Taxes Table")
    render_datatable(
        conn, 
        'order_taxes', 
        'order_tax_id', 
        ['order_tax_id', 'tax_amount', 'tax_rate', 'tax_title', 'order_id'],
        'taxes',
        search_columns=['tax_title', 'order_id']
    )

# Tab 11: Order Discounts
with tab11:
    st.header("Order Discounts Table")
    render_datatable(
        conn, 
        'order_discounts', 
        'order_discount_id', 
        ['order_discount_id', 'discount_amount', 'discount_rate', 'discount_title', 'order_id'],
        'discounts',
        search_columns=['discount_title', 'order_id']
    )
