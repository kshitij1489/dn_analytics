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
import altair as alt
import time
import traceback

# --- CONSTANTS ---
INDIAN_HOLIDAYS = [
    # 2025
    {"date": "2025-01-26", "name": "Republic Day 🇮🇳"},
    {"date": "2025-02-26", "name": "Maha Shivratri"},
    {"date": "2025-03-14", "name": "Holi"},
    {"date": "2025-03-31", "name": "Id-ul-Fitr (Eid)"},
    {"date": "2025-04-10", "name": "Mahavir Jayanti"},
    {"date": "2025-04-14", "name": "Dr. B. R. Ambedkar Jayanti"},
    {"date": "2025-04-18", "name": "Good Friday"},
    {"date": "2025-05-12", "name": "Buddha Purnima"},
    {"date": "2025-06-07", "name": "Id-ul-Zuha (Bakrid)"},
    {"date": "2025-07-06", "name": "Muharram"},
    {"date": "2025-08-15", "name": "Independence Day 🇮🇳"},
    {"date": "2025-08-16", "name": "Janmashtami"},
    {"date": "2025-10-02", "name": "Gandhi Jayanti 🇮🇳"},
    {"date": "2025-10-20", "name": "Diwali (Deepavali)"},
    {"date": "2025-11-05", "name": "Guru Nanak Jayanti"},
    {"date": "2025-12-25", "name": "Christmas"},
    # 2026
    {"date": "2026-01-26", "name": "Republic Day 🇮🇳"},
    {"date": "2026-03-04", "name": "Holi"},
    {"date": "2026-03-21", "name": "Id-ul-Fitr (Eid)"},
    {"date": "2026-03-26", "name": "Ram Navami"},
    {"date": "2026-03-31", "name": "Mahavir Jayanti"},
    {"date": "2026-04-03", "name": "Good Friday"},
    {"date": "2026-05-01", "name": "Buddha Purnima"},
    {"date": "2026-05-27", "name": "Id-ul-Zuha (Bakrid)"},
    {"date": "2026-06-26", "name": "Muharram"},
    {"date": "2026-08-15", "name": "Independence Day 🇮🇳"},
    {"date": "2026-08-26", "name": "Milad-un-Nabi / Id-e-Milad"},
    {"date": "2026-09-04", "name": "Janmashtami"},
    {"date": "2026-10-02", "name": "Gandhi Jayanti 🇮🇳"},
    {"date": "2026-10-20", "name": "Dussehra"},
    {"date": "2026-11-08", "name": "Diwali"},
    {"date": "2026-11-24", "name": "Guru Nanak Jayanti"},
    {"date": "2026-12-25", "name": "Christmas"},
]

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

# Custom CSS for Navigation Buttons
st.markdown("""
<style>
    /* Premium styling ONLY for the navigation buttons at the top of the sidebar */
    /* We target the first 8 children of the sidebar vertical block to avoid hitting the Sync button */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(-n+8) button[kind="primary"] {
        background-color: #BAE6FD !important; /* Premium Light Blue */
        color: #0369A1 !important;           /* Darker Blue Text */
        border: 1px solid #7DD3FC !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06) !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
        border-radius: 8px !important;
    }
    
    /* Hover effect for navigation buttons */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(-n+8) button[kind="primary"]:hover {
        background-color: #7DD3FC !important;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05) !important;
        transform: translateY(-1px) !important;
    }
    
    /* Rounded corners for all sidebar buttons */
    [data-testid="stSidebar"] button {
        border-radius: 8px !important;
    }
</style>
""", unsafe_allow_html=True)

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
if 'db_status' not in st.session_state:
    st.session_state.db_status = "Disconnected" # Disconnected, Connecting, Connected, Failed
if 'active_module' not in st.session_state:
    st.session_state.active_module = "🏠 Insights"

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
            
            # 1. Auto-seed if empty
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM menu_items")
            count = cursor.fetchone()[0]
            cursor.close()
            
            if count == 0:
                perform_seeding(conn)
            
            # 2. Initialize cluster
            if st.session_state.item_cluster is None:
                st.session_state.item_cluster = OrderItemCluster(conn)
            
            # Final check - if we reached here, both are okay
            st.session_state.db_status = "Connected"
            st.session_state.initialized = True
        except Exception as e:
            st.session_state.db_status = "Failed"
            st.error(f"Initialization failed: {e}")



def get_db_connection(force_retry=False):
    """
    Get or create database connection with retry logic.
    Returns the connection and the status.
    """
    if not force_retry and st.session_state.db_conn is not None and not st.session_state.db_conn.closed:
        # If we have a connection, verify schema too if not already "Connected"
        if st.session_state.db_status != "Connected":
             st.session_state.db_status = "Connecting"
             if check_schema_exists(st.session_state.db_conn):
                 st.session_state.db_status = "Connected"
             else:
                 st.session_state.db_status = "Failed"
        return st.session_state.db_conn

    st.session_state.db_status = "Connecting"
    
    # Try up to 3 times
    for attempt in range(3):
        try:
            db_url = None
            try:
                db_url = st.secrets.get("database", {}).get("url")
            except:
                pass
            
            if not db_url:
                db_url = os.environ.get("DB_URL")
            
            if not db_url:
                db_url = "postgresql://kshitijsharma@localhost:5432/analytics"
            
            conn = psycopg2.connect(db_url, connect_timeout=3)
            
            # If connection succeeds, return it
            st.session_state.db_conn = conn
            st.session_state.db_connected = True
            # Status will be updated to "Connected" by initialize_app once schema is verified
            return conn
            
        except Exception:
            if attempt == 2: # Last attempt
                st.session_state.db_status = "Failed"
                st.session_state.db_connected = False
                return None
            import time
            time.sleep(1) # Wait a bit before retry
            
    return None

def format_indian_currency(number):
    """Format number with Indian nomenclature (Lakhs, Crores) without decimals as requested"""
    try:
        if number is None: return "0"
        s = str(int(float(number)))
        if len(s) <= 3: return s
        last_three = s[-3:]
        others = s[:-3]
        others_reversed = others[::-1]
        pairs = [others_reversed[i:i+2] for i in range(0, len(others_reversed), 2)]
        formatted_others = ",".join(pairs)[::-1]
        return f"{formatted_others},{last_three}"
    except:
        return str(number)

def get_dataframe_config(df):
    """Generate dynamic st.column_config for a dataframe based on column names"""
    config = {}
    for col in df.columns:
        c_low = col.lower()
        
        # ID Columns (No commas)
        if "_id" in c_low or c_low == "id" or c_low.endswith("id"):
            config[col] = st.column_config.NumberColumn(format="%d")
            
        # Currency/Money (Standard grouping with prefix)
        elif any(x in c_low for x in ["revenue", "spent", "price", "total", "tax", "discount", "core", "amount"]):
            if "count" not in c_low: # Avoid mis-identifying counts as money
                config[col] = st.column_config.NumberColumn(format="₹%d")
            else:
                config[col] = st.column_config.NumberColumn(format="%d")
                
        # Percentages
        elif "rate" in c_low or "percent" in c_low or "%" in col:
            config[col] = st.column_config.NumberColumn(format="%.1f%%")
            
        # Counts/Quantities
        elif any(x in c_low for x in ["count", "quantity", "total_sold", "Home Website", "POS", "Swiggy", "Zomato"]):
            config[col] = st.column_config.NumberColumn(format="%d")
            
    return config


def check_schema_exists(conn):
    """Check if the required schema tables exist"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'orders'
            )
        """)
        exists = cursor.fetchone()[0]
        cursor.close()
        return exists
    except:
        return False


def render_resolutions_tab(conn):
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


def render_insights_dashboard(conn):
    st.header("🏠 Executive Insights")
    
    # 1. KPIs
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT 
            COUNT(*) as total_orders,
            SUM(total) as total_revenue,
            AVG(total) as avg_order_value,
            (SELECT COUNT(*) FROM customers) as total_customers
        FROM orders
    """)
    kpis = cursor.fetchone()
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Revenue", f"₹{format_indian_currency(kpis['total_revenue'])}")
    c2.metric("Orders", f"{kpis['total_orders']:,}")
    c3.metric("Avg Order", f"₹{format_indian_currency(kpis['avg_order_value'])}")
    c4.metric("Customers", f"{kpis['total_customers']:,}")
    
    st.markdown("---")
    
    # 2. Insights Tabs
    tab_ds, tab_menu, tab_cust, tab_charts = st.tabs(["Daily Sales", "Menu Items", "Customer", "Charts"])
    
    with tab_ds:
        st.subheader("� Daily Sales Performance")
        
        cursor.execute("""
            SELECT 
                DATE(created_on) as order_date,
                SUM(total) as total_revenue,
                SUM(total - tax_total) as net_revenue,
                SUM(tax_total) as tax_collected,
                COUNT(*) as total_orders,
                SUM(total) FILTER (WHERE order_from = 'Home Website') as "Website Revenue",
                SUM(total) FILTER (WHERE order_from = 'POS') as "POS Revenue",
                SUM(total) FILTER (WHERE order_from = 'Swiggy') as "Swiggy Revenue",
                SUM(total) FILTER (WHERE order_from = 'Zomato') as "Zomato Revenue"
            FROM orders
            WHERE order_status = 'Success'
            GROUP BY DATE(created_on)
            ORDER BY order_date DESC
        """)
        ds_data = pd.DataFrame(cursor.fetchall())
        
        if not ds_data.empty:
            # Format currency columns with Indian nomenclature (Strings break sorting, but user requested this format)
            cols_to_format = ['total_revenue', 'net_revenue', 'tax_collected']
            for col in cols_to_format:
                ds_data[col] = ds_data[col].apply(lambda x: f"₹{format_indian_currency(x)}")
            
            # Use Column Config for counts to ensure proper numeric sorting
            st.dataframe(
                ds_data, 
                use_container_width=True, 
                height=600,
                column_config={
                    "order_date": "Date",
                    "total_revenue": "Total Revenue",
                    "net_revenue": "Net Revenue",
                    "tax_collected": "Tax Collected",
                    "total_orders": st.column_config.NumberColumn("Total Orders", format="%d"),
                    "Website Revenue": st.column_config.NumberColumn(format="₹%d"),
                    "POS Revenue": st.column_config.NumberColumn(format="₹%d"),
                    "Swiggy Revenue": st.column_config.NumberColumn(format="₹%d"),
                    "Zomato Revenue": st.column_config.NumberColumn(format="₹%d")
                }
            )
        else:
            st.info("No daily sales data available.")

    with tab_menu:
        st.subheader("🍱 Menu Analytics")
        
        # Filters for menu items
        c1, c2 = st.columns(2)
        with c1:
            name_search = st.text_input("Search by Item Name:", key="menu_name_filter", placeholder="e.g. Chocolate")
        with c2:
            cursor.execute("SELECT DISTINCT type FROM menu_items ORDER BY type")
            types = [t['type'] for t in cursor.fetchall()]
            type_choice = st.selectbox("Filter by Type:", ["All"] + types, key="menu_type_filter")

        # Build query filters for the WHERE clause inside reorder_stats or CTE
        # Note: We filter early in the CTE for performance if possible, 
        # but for clean grouping we'll apply it to the final stats if name/type are involved.
        filter_sql = ""
        query_params = []
        if name_search:
            filter_sql += " AND item_name ILIKE %s"
            query_params.append(f"%{name_search}%")
        if type_choice != "All":
            filter_sql += " AND item_type = %s"
            query_params.append(type_choice)

        menu_query = f"""
            WITH dedup_items AS (
                SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                    oi.order_item_id, 
                    oi.menu_item_id, 
                    oi.total_price,
                    oi.quantity,
                    oi.order_id
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.order_id
                WHERE o.order_status = 'Success'
            ),
            dedup_addons AS (
                 SELECT DISTINCT ON (oia.order_item_id, oia.name_raw, oia.quantity, oia.price) 
                    oia.menu_item_id, 
                    (oia.price * oia.quantity) as total_price,
                    oia.quantity,
                    oi.order_id,
                    oi.order_item_id
                FROM order_item_addons oia
                JOIN dedup_items oi ON oia.order_item_id = oi.order_item_id
            ),
            customer_item_orders AS (
                -- Base items (ALL confirmed statuses, NO user filter)
                SELECT 
                    mi.menu_item_id,
                    mi.name AS item_name,
                    mi.type AS item_type,
                    o.customer_id,
                    COUNT(DISTINCT o.order_id) AS order_occurrence_count,
                    SUM(di.total_price) AS item_revenue,
                    SUM(di.quantity) as sold_as_item_qty,
                    0 as sold_as_addon_qty
                FROM dedup_items di
                JOIN orders o ON di.order_id = o.order_id
                JOIN menu_items mi ON di.menu_item_id = mi.menu_item_id
                JOIN customers c ON o.customer_id = c.customer_id
                WHERE o.order_status = 'Success' 
                GROUP BY mi.menu_item_id, mi.name, mi.type, o.customer_id
                
                UNION ALL
                
                -- Addons (confirmed users)
                SELECT 
                    mi.menu_item_id,
                    mi.name AS item_name,
                    mi.type AS item_type,
                    o.customer_id,
                    COUNT(DISTINCT o.order_id) AS order_occurrence_count,
                    SUM(da.total_price) AS item_revenue,
                    0 as sold_as_item_qty,
                    SUM(da.quantity) as sold_as_addon_qty
                FROM dedup_addons da
                JOIN dedup_items di ON da.order_item_id = di.order_item_id
                JOIN orders o ON di.order_id = o.order_id
                JOIN menu_items mi ON da.menu_item_id = mi.menu_item_id
                JOIN customers c ON o.customer_id = c.customer_id
                WHERE o.order_status = 'Success' 
                GROUP BY mi.menu_item_id, mi.name, mi.type, o.customer_id
            ),
            aggregated_customer_item AS (
                SELECT 
                    menu_item_id, item_name, item_type, customer_id,
                    SUM(order_occurrence_count) as total_order_occurrences,
                    SUM(item_revenue) as total_item_revenue,
                    SUM(sold_as_item_qty) as total_sold_as_item,
                    SUM(sold_as_addon_qty) as total_sold_as_addon
                FROM customer_item_orders
                GROUP BY menu_item_id, item_name, item_type, customer_id
            ),
            reorder_stats AS (
                SELECT 
                    menu_item_id, item_name, item_type,
                    SUM(total_sold_as_item) as sold_as_item,
                    SUM(total_sold_as_addon) as sold_as_addon,
                    SUM(total_order_occurrences - 1) FILTER (WHERE total_order_occurrences > 1) AS total_reorders,
                    COUNT(*) FILTER (WHERE total_order_occurrences > 1) AS customers_who_reordered,
                    COUNT(*) AS total_unique_customers,
                    (SUM(total_sold_as_item) + SUM(total_sold_as_addon)) AS total_qty_sold,
                    SUM(total_order_occurrences) AS total_orders,
                    SUM(total_item_revenue) AS total_revenue,
                    SUM(total_item_revenue) FILTER (WHERE total_order_occurrences > 1) AS repeat_customer_revenue
                FROM aggregated_customer_item
                WHERE 1=1 {filter_sql}
                GROUP BY menu_item_id, item_name, item_type
            )
            SELECT 
                item_name as "Item Name",
                item_type as "Type",
                sold_as_addon as "As Addon (Qty)",
                sold_as_item as "As Item (Qty)",
                total_qty_sold as "Total Sold (Qty)",
                total_revenue as "Total Revenue",
                total_reorders AS "Reorder Count",
                customers_who_reordered AS "Repeat Customers",
                total_unique_customers AS "Unique Customers",
                ROUND(100.0 * customers_who_reordered / NULLIF(total_unique_customers, 0), 2) AS "Reorder Rate %%",
                ROUND(100.0 * repeat_customer_revenue / NULLIF(total_revenue, 0), 2) AS "Repeat Revenue %%"
            FROM reorder_stats
            WHERE total_unique_customers > 0
            ORDER BY total_revenue DESC;
        """
        
        cursor.execute(menu_query, query_params)
        menu_data = pd.DataFrame(cursor.fetchall())
        
        if not menu_data.empty:
            # Format currency (needs to stay string for Indian nomenclature)
            menu_data['Total Revenue'] = menu_data['Total Revenue'].apply(lambda x: f"₹{format_indian_currency(x)}")
            
            st.dataframe(
                menu_data, 
                use_container_width=True, 
                height=600,
                column_config={
                    "Reorder Rate %": st.column_config.NumberColumn(
                        "Reorder Rate",
                        help="Percentage of identified customers who reordered this item",
                        format="%.1f%%"
                    ),
                    "Repeat Revenue %": st.column_config.NumberColumn(
                        "Repeat Revenue",
                        help="Percentage of total revenue from repeat customers",
                        format="%.1f%%"
                    ),
                    "Repeat Customers": st.column_config.NumberColumn(format="%d"),
                    "Unique Customers": st.column_config.NumberColumn(format="%d"),
                    "Total Sold (Qty)": st.column_config.NumberColumn("Total Units", format="%d"),
                    "As Item (Qty)": st.column_config.NumberColumn("As Item", format="%d"),
                    "As Addon (Qty)": st.column_config.NumberColumn("As Addon", format="%d"),
                    "Reorder Count": st.column_config.NumberColumn(format="%d")
                }
            )
        else:
            st.info("No menu analytics found for current filters (excluding Anonymous users).")
        
    with tab_cust:
        # Removed Customer Insights subheader
        
        # 1. Reorder Rates (Global)
        cursor.execute("""
            WITH customer_stats AS (
                SELECT 
                    total_orders,
                    CASE WHEN total_orders > 1 THEN 1 ELSE 0 END as is_returning
                FROM customers c
                WHERE (c.phone IS NOT NULL AND c.phone != '') 
                OR (c.name IS NOT NULL AND c.name != '' AND c.name != 'Anonymous' AND c.address IS NOT NULL AND c.address != '')
            )
            SELECT 
                COUNT(*) as total_customers,
                SUM(is_returning) as returning_customers,
                (CAST(SUM(is_returning) AS FLOAT) / NULLIF(COUNT(*), 0)) * 100 as reorder_rate
            FROM customer_stats
        """)
        reorder = cursor.fetchone()
        
        if reorder and reorder['total_customers'] > 0:
            cr1, cr2, cr3 = st.columns(3)
            # Safe metrics with fallback to 0
            cust_count = reorder['total_customers'] or 0
            ret_count = reorder['returning_customers'] or 0
            rate = reorder['reorder_rate'] or 0
            
            cr1.metric("Total Customers (Verified)", f"{cust_count:,}")
            cr2.metric("Returning Customers", f"{ret_count:,}")
            cr3.metric("Return Customer Rate", f"{rate:.1f}%")
        else:
            st.info("No customer loyalty data yet.")
            
        st.markdown("---")
        
        # 2. Top Customers Table
        st.subheader("💎 Top Customers by Spend")
        cursor.execute("""
            WITH customer_item_counts AS (
                -- Main Items
                SELECT 
                    o.customer_id,
                    mi.name as item_name,
                    SUM(oi.quantity) as item_qty
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.order_id
                JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
                WHERE o.order_status = 'Success'
                GROUP BY o.customer_id, mi.name
                
                UNION ALL
                
                -- Addons
                SELECT 
                    o.customer_id,
                    mi.name as item_name,
                    SUM(oia.quantity) as item_qty
                FROM order_item_addons oia
                JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                JOIN orders o ON oi.order_id = o.order_id
                JOIN menu_items mi ON oia.menu_item_id = mi.menu_item_id
                WHERE o.order_status = 'Success'
                GROUP BY o.customer_id, mi.name
            ),
            final_counts AS (
                SELECT 
                    customer_id,
                    item_name,
                    SUM(item_qty) as total_item_qty
                FROM customer_item_counts
                GROUP BY customer_id, item_name
            ),
            top_items_per_customer AS (
                SELECT DISTINCT ON (customer_id)
                    customer_id,
                    item_name,
                    total_item_qty
                FROM final_counts
                ORDER BY customer_id, total_item_qty DESC, item_name ASC
            )
            SELECT 
                c.name,
                c.total_orders,
                c.total_spent,
                c.last_order_date,
                CASE WHEN c.total_orders > 1 THEN 'Returning' ELSE 'New' END as status,
                tic.item_name as favorite_item,
                tic.total_item_qty as fav_item_qty
            FROM customers c
            LEFT JOIN top_items_per_customer tic ON c.customer_id = tic.customer_id
            WHERE (c.name IS NOT NULL AND c.name != '' AND c.name != 'Anonymous' AND c.address IS NOT NULL AND c.address != '')
            OR (c.phone IS NOT NULL AND c.phone != '')
            ORDER BY c.total_spent DESC
            LIMIT 50
        """)
        top_cust = pd.DataFrame(cursor.fetchall())
        
        if not top_cust.empty:
            # Format date for display
            top_cust['last_order_date'] = pd.to_datetime(top_cust['last_order_date']).dt.strftime('%Y-%m-%d %I:%M %p')
            
            st.dataframe(
                top_cust, 
                use_container_width=True, 
                height=500,
                column_config={
                    "name": "Customer Name",
                    "favorite_item": "Favorite Item",
                    "fav_item_qty": st.column_config.NumberColumn("Favorite Count", format="%d"),
                    "total_orders": st.column_config.NumberColumn("Total Orders", format="%d"),
                    "total_spent": st.column_config.NumberColumn(
                        "Total Earned", 
                        help="Total spend across all successful orders",
                        format="₹%d"
                    ),
                    "last_order_date": "Last Seen",
                    "status": "Loyalty Status"
                }
            )
        else:
            st.info("No top customer data available.")

    with tab_charts:
        # Removed Visual Analytics subheader
        
        chart_to_show = st.selectbox(
            "Select Visualization:",
            ["📈 Daily Sales Trend", "📉 Sales by Category Trend", "🖇️ Revenue vs Orders", "📊 Average Order Value Trend", "🏆 Top 10 Items", "📂 Revenue by Category", "⏰ Hourly Revenue Analysis", "🛵 Order Source"],
            index=0,
            key="insights_chart_selector"
        )
        
        st.markdown("---")

        if chart_to_show in ["📈 Daily Sales Trend", "📉 Sales by Category Trend", "🖇️ Revenue vs Orders", "📊 Average Order Value Trend"]:
            # Chart Controls
            c_filter1, c_filter2 = st.columns(2)
            
            with c_filter1:
                # Metric always comes first now
                if chart_to_show in ["📈 Daily Sales Trend", "📉 Sales by Category Trend"]:
                    agg_metric = st.selectbox(
                        "Metric:", 
                        ["Total", "Average", "Cumulative", "Moving Average (7-day)"], 
                        index=3,
                        key=f"chart_agg_metric_{chart_to_show}" # Unique key per chart view
                    )
                else:
                    st.empty()
            
            with c_filter2:
                # Time Bucket layout to include Toggle
                c_bucket, c_holiday = st.columns([2, 1])
                with c_bucket:
                    # Time Bucket is only relevant for non-Moving Average views
                    is_sma = (chart_to_show == "📈 Daily Sales Trend" and agg_metric == "Moving Average (7-day)")
                    time_bucket = st.selectbox(
                        "Time Bucket:", 
                        ["Day", "Week", "Month"], 
                        index=0,
                        key=f"chart_time_bucket_{chart_to_show}",
                        disabled=is_sma,
                        help="Disabled for Moving Average (calculates daily)" if is_sma else None
                    )
                    if is_sma: 
                        time_bucket = "Day" # Force Day for SMA
                
                with c_holiday:
                    st.write("") # Alignment
                    show_holidays = st.toggle("Holidays", value=False, key=f"chart_show_holidays_{chart_to_show}")
                
            # Weekday Toggles
            st.write("Include Days:")
            days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            days_abbr = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            
            if 'selected_weekdays' not in st.session_state:
                st.session_state.selected_weekdays = days_of_week.copy()
                
            weekday_cols = st.columns(7)
            for i, day in enumerate(days_of_week):
                is_selected = day in st.session_state.selected_weekdays
                if weekday_cols[i].button(
                    days_abbr[i], 
                    key=f"btn_day_{day}_{chart_to_show}_v2", # Updated key to avoid conflicts
                    use_container_width=True,
                    type="primary" if is_selected else "secondary"
                ):
                    if is_selected:
                        st.session_state.selected_weekdays.remove(day)
                    else:
                        st.session_state.selected_weekdays.append(day)
                    st.rerun()

            # Fetch Data
            cursor.execute("""
                SELECT 
                    DATE(created_on) as date,
                    SUM(total) as revenue,
                    COUNT(*) as num_orders
                FROM orders
                WHERE order_status = 'Success'
                GROUP BY DATE(created_on)
                ORDER BY date
            """)
            chart_data = pd.DataFrame(cursor.fetchall())
            
            if not chart_data.empty:
                chart_data['date'] = pd.to_datetime(chart_data['date'])
                chart_data['revenue'] = chart_data['revenue'].astype(float)
                chart_data['num_orders'] = chart_data['num_orders'].astype(int)
                chart_data['day_name'] = chart_data['date'].dt.day_name()
                
                # 1. First, Filter by Weekdays
                filtered_df = chart_data[chart_data['day_name'].isin(st.session_state.selected_weekdays)].copy()
                
                if not filtered_df.empty:
                    # 2. Resample/Bucket
                    filtered_df.set_index('date', inplace=True)
                    bucket_map = {"Day": "D", "Week": "W", "Month": "M"}
                    freq = bucket_map.get(time_bucket, "D")
                    
                    # 3. Add Holiday Layer Logic
                    holiday_layer = None
                    if show_holidays:
                        min_date = filtered_df.index.min()
                        max_date = filtered_df.index.max()
                        relevant_holidays = [h for h in INDIAN_HOLIDAYS if pd.to_datetime(h['date']) >= min_date and pd.to_datetime(h['date']) <= max_date]
                        
                        if relevant_holidays:
                            h_df = pd.DataFrame(relevant_holidays)
                            h_df['date'] = pd.to_datetime(h_df['date'])
                            holiday_layer = alt.Chart(h_df).mark_rule(
                                color='#F59E0B', 
                                strokeDash=[4, 4],
                                size=2
                            ).encode(
                                x='date:T',
                                tooltip=alt.Tooltip('name:N', title='Holiday')
                            )

                    if chart_to_show == "📈 Daily Sales Trend":
                        if agg_metric == "Total":
                            plot_df = filtered_df['revenue'].resample(freq).sum()
                        elif agg_metric == "Average":
                            plot_df = filtered_df['revenue'].resample(freq).mean()
                        elif agg_metric == "Cumulative":
                            plot_df = filtered_df['revenue'].resample(freq).sum().cumsum()
                        elif agg_metric == "Moving Average (7-day)":
                            plot_df = filtered_df['revenue'].resample('D').sum().rolling(window=7).mean()
                        
                        metric_name = f"{agg_metric} Realized Revenue (₹)"
                        plot_data = plot_df.reset_index()
                        plot_data.columns = ['date', 'value']
                        
                        line = alt.Chart(plot_data).mark_line(color='#3B82F6', strokeWidth=3).encode(
                            x=alt.X('date:T', title='Date'),
                            y=alt.Y('value:Q', title=metric_name),
                            tooltip=[alt.Tooltip('date:T', title='Date'), alt.Tooltip('value:Q', title=metric_name, format=',.2f')]
                        )
                        
                        points = line.mark_point(color='#3B82F6', size=60).encode(
                            opacity=alt.condition(alt.datum.value > 0, alt.value(1), alt.value(0))
                        )
                        
                        chart = alt.layer(line, points).properties(height=500)
                        if holiday_layer:
                            chart = alt.layer(chart, holiday_layer)
                            
                        st.altair_chart(chart.interactive(), use_container_width=True)
                        
                    elif chart_to_show == "📉 Sales by Category Trend":
                        # Fetch Category Data
                        cursor.execute("""
                            SELECT 
                                DATE(o.created_on) as date,
                                mi.type as category,
                                SUM(oi.total_price) as revenue
                            FROM orders o
                            JOIN order_items oi ON o.order_id = oi.order_id
                            JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
                            WHERE o.order_status = 'Success'
                            GROUP BY DATE(o.created_on), mi.type
                            ORDER BY date
                        """)
                        cat_data = pd.DataFrame(cursor.fetchall())
                        
                        if not cat_data.empty:
                            cat_data['date'] = pd.to_datetime(cat_data['date'])
                            cat_data['revenue'] = cat_data['revenue'].astype(float)
                            cat_data['day_name'] = cat_data['date'].dt.day_name()
                            
                            # Filter by Weekdays
                            cat_filtered = cat_data[cat_data['day_name'].isin(st.session_state.selected_weekdays)].copy()
                            
                            if not cat_filtered.empty:
                                # Resample/Bucket
                                cat_filtered.set_index('date', inplace=True)
                                
                                # We need to group by category and then resample
                                plot_list = []
                                for cat, group in cat_filtered.groupby('category'):
                                    if agg_metric == "Total":
                                        resampled = group['revenue'].resample(freq).sum()
                                    elif agg_metric == "Average":
                                        resampled = group['revenue'].resample(freq).mean()
                                    elif agg_metric == "Cumulative":
                                        resampled = group['revenue'].resample(freq).sum().cumsum()
                                    elif agg_metric == "Moving Average (7-day)":
                                        resampled = group['revenue'].resample('D').sum().rolling(window=7).mean()
                                    
                                    rdf = resampled.reset_index()
                                    rdf['category'] = cat
                                    plot_list.append(rdf)
                                
                                plot_data = pd.concat(plot_list)
                                plot_data.columns = ['date', 'value', 'category']
                                
                                line = alt.Chart(plot_data).mark_line(strokeWidth=3).encode(
                                    x=alt.X('date:T', title='Date'),
                                    y=alt.Y('value:Q', title=f"{agg_metric} Realized Revenue (₹)"),
                                    color=alt.Color('category:N', title='Category'),
                                    tooltip=[alt.Tooltip('date:T', title='Date'), alt.Tooltip('category:N'), alt.Tooltip('value:Q', title='Revenue (₹)', format=',.2f')]
                                )
                                
                                chart = line.properties(height=500)
                                if holiday_layer:
                                    chart = alt.layer(line, holiday_layer)
                                    
                                st.altair_chart(chart.interactive(), use_container_width=True)
                            else:
                                st.warning("No data found for selected weekday filters.")
                        else:
                            st.info("No sales data available.")

                    elif chart_to_show == "🖇️ Revenue vs Orders":
                        st.markdown("**🖇️ Revenue vs Order Volume**")
                        # Recalculate resampled data for this specific dual-axis view
                        agg_df = filtered_df.resample(freq).agg({'revenue': 'sum', 'num_orders': 'sum'}).reset_index()
                        
                        base = alt.Chart(agg_df).encode(
                            x=alt.X('date:T', title='Time Period')
                        )
                        
                        rev_line = base.mark_line(color='#FF4B4B', strokeWidth=3).encode(
                            y=alt.Y('revenue:Q', title='Revenue (₹)', axis=alt.Axis(titleColor='#FF4B4B')),
                            tooltip=[alt.Tooltip('date:T', title='Date'), alt.Tooltip('revenue:Q', title='Revenue (₹)', format=',')]
                        )
                        
                        order_line = base.mark_line(color='#4B4BFF', strokeDash=[5,5], strokeWidth=2).encode(
                            y=alt.Y('num_orders:Q', title='Number of Orders', axis=alt.Axis(titleColor='#4B4BFF')),
                            tooltip=[alt.Tooltip('date:T', title='Date'), alt.Tooltip('num_orders:Q', title='Orders')]
                        )
                        
                        chart = alt.layer(rev_line, order_line).resolve_scale(y='independent').properties(height=500)
                        if holiday_layer:
                            chart = alt.layer(chart, holiday_layer)
                            
                        st.altair_chart(chart, use_container_width=True)
                        st.caption("🔴 Revenue (Solid) | 🔵 Orders (Dashed) | 🟠 Holidays (Dashed)")
                        
                    elif chart_to_show == "📊 Average Order Value Trend":
                        st.markdown("**📊 Average Order Value (AOV) Trend**")
                        agg_df = filtered_df.resample(freq).agg({'revenue': 'sum', 'num_orders': 'sum'}).reset_index()
                        agg_df['aov'] = agg_df['revenue'] / agg_df['num_orders'].replace(0, 1)
                        
                        line = alt.Chart(agg_df).mark_line(point=True, color='#00CC96', strokeWidth=3).encode(
                            x=alt.X('date:T', title='Time Period'),
                            y=alt.Y('aov:Q', title='Average Order Value (₹)'),
                            tooltip=[
                                alt.Tooltip('date:T', title='Period'),
                                alt.Tooltip('aov:Q', title='AOV (₹)', format=',.2f'),
                                alt.Tooltip('revenue:Q', title='Total Rev (₹)', format=','),
                                alt.Tooltip('num_orders:Q', title='Total Orders')
                            ]
                        )
                        
                        chart = line
                        if holiday_layer:
                            chart = alt.layer(line, holiday_layer)
                            
                        st.altair_chart(chart.interactive().properties(height=500), use_container_width=True)
                        st.caption("AOV = Total Revenue ÷ Total Orders (Higher AOV indicates successful upselling or premium items)")
                else:
                    st.warning("No data found for selected weekday filters.")

        elif chart_to_show == "🏆 Top 10 Items":
            st.markdown("**🏆 Top 10 Most Sold Items**")
            
            # 1. Get total system revenue (Deduplicated & Success Only)
            cursor.execute("""
                WITH dedup_items AS (
                    SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                        oi.order_item_id, oi.total_price
                    FROM order_items oi
                    JOIN orders o ON oi.order_id = o.order_id
                    WHERE o.order_status = 'Success'
                ),
                dedup_addons AS (
                    SELECT DISTINCT ON (oia.order_item_id, oia.name_raw, oia.quantity, oia.price) 
                        oia.order_item_id, (oia.price * oia.quantity) as rev
                    FROM order_item_addons oia
                    JOIN dedup_items di ON oia.order_item_id = di.order_item_id
                ),
                item_rev AS (
                    SELECT SUM(total_price) as rev FROM dedup_items
                    UNION ALL
                    SELECT SUM(rev) as rev FROM dedup_addons
                )
                SELECT SUM(rev) FROM item_rev
            """)
            total_system_revenue = float(cursor.fetchone()['sum'] or 0)
            
            # 2. Get top 10 items by sold count with their revenue (Deduplicated)
            cursor.execute("""
                WITH dedup_items AS (
                    SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                        oi.order_item_id, oi.menu_item_id, oi.total_price
                    FROM order_items oi
                    JOIN orders o ON oi.order_id = o.order_id
                    WHERE o.order_status = 'Success'
                ),
                item_rev_combined AS (
                    SELECT di.menu_item_id, SUM(di.total_price) as rev
                    FROM dedup_items di
                    GROUP BY di.menu_item_id
                    UNION ALL
                    SELECT oia.menu_item_id, SUM(oia.price * oia.quantity) as rev
                    FROM order_item_addons oia
                    JOIN dedup_items di ON oia.order_item_id = di.order_item_id
                    GROUP BY oia.menu_item_id
                ),
                item_totals AS (
                    SELECT menu_item_id, SUM(rev) as rev
                    FROM item_rev_combined
                    GROUP BY menu_item_id
                )
                SELECT mi.name, mi.total_sold, it.rev as item_revenue
                FROM menu_items mi
                LEFT JOIN item_totals it ON mi.menu_item_id = it.menu_item_id
                WHERE mi.is_active = TRUE
                ORDER BY mi.total_sold DESC 
                LIMIT 10
            """)
            top_items = pd.DataFrame(cursor.fetchall())
            
            if not top_items.empty and total_system_revenue > 0:
                # Calculate percentages
                top_items['item_revenue'] = top_items['item_revenue'].fillna(0).astype(float)
                top_items['rev_pct'] = (top_items['item_revenue'] / total_system_revenue) * 100
                top_items['pct_label'] = top_items['rev_pct'].apply(lambda x: f"{x:.1f}%")

                # Create the bar chart for total_sold
                bars = alt.Chart(top_items).mark_bar().encode(
                    x=alt.X('name:N', title='Item', sort='-y'),
                    y=alt.Y('total_sold:Q', title='Quantity Sold'),
                    color=alt.Color('name:N', legend=None),
                    tooltip=[
                        alt.Tooltip('name:N', title='Item'),
                        alt.Tooltip('total_sold:Q', title='Quantity Sold'),
                        alt.Tooltip('pct_label:N', title='Revenue %')
                    ]
                )

                # Create text labels for revenue %
                text = bars.mark_text(
                    align='center',
                    baseline='bottom',
                    dy=-10,
                    fontWeight='bold'
                ).encode(
                    text=alt.Text('pct_label:N')
                )

                st.altair_chart((bars + text).properties(height=500), use_container_width=True)
                st.caption(f"Labels show % of total system revenue (Total: ₹{format_indian_currency(total_system_revenue)})")
            else:
                st.info("No item sales data available.")

        elif chart_to_show == "📂 Revenue by Category":
            st.markdown("**📂 Revenue by Category**")
            
            # 1. Get total system revenue (Deduplicated & Success Only)
            cursor.execute("""
                WITH dedup_items AS (
                    SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                        oi.order_item_id, oi.total_price
                    FROM order_items oi
                    JOIN orders o ON oi.order_id = o.order_id
                    WHERE o.order_status = 'Success'
                ),
                dedup_addons AS (
                    SELECT DISTINCT ON (oia.order_item_id, oia.name_raw, oia.quantity, oia.price) 
                        oia.order_item_id, (oia.price * oia.quantity) as rev
                    FROM order_item_addons oia
                    JOIN dedup_items di ON oia.order_item_id = di.order_item_id
                ),
                item_rev AS (
                    SELECT SUM(total_price) as rev FROM dedup_items
                    UNION ALL
                    SELECT SUM(rev) as rev FROM dedup_addons
                )
                SELECT SUM(rev) FROM item_rev
            """)
            total_system_revenue = float(cursor.fetchone()['sum'] or 0)
            
            # 2. Get revenue by category (Deduplicated)
            cursor.execute("""
                WITH dedup_items AS (
                    SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                        oi.order_item_id, oi.menu_item_id, oi.total_price
                    FROM order_items oi
                    JOIN orders o ON oi.order_id = o.order_id
                    WHERE o.order_status = 'Success'
                ),
                item_rev_combined AS (
                    SELECT di.menu_item_id, di.total_price as rev
                    FROM dedup_items di
                    UNION ALL
                    SELECT oia.menu_item_id, (oia.price * oia.quantity) as rev
                    FROM order_item_addons oia
                    JOIN dedup_items di ON oia.order_item_id = di.order_item_id
                ),
                cat_rev AS (
                    SELECT mi.type as category, SUM(irc.rev) as revenue
                    FROM item_rev_combined irc
                    JOIN menu_items mi ON irc.menu_item_id = mi.menu_item_id
                    WHERE mi.type IS NOT NULL AND mi.type != ''
                    GROUP BY mi.type
                )
                SELECT category, revenue
                FROM cat_rev
                ORDER BY revenue DESC
            """)
            cat_data = pd.DataFrame(cursor.fetchall())
            
            if not cat_data.empty and total_system_revenue > 0:
                cat_data['revenue'] = cat_data['revenue'].astype(float)
                cat_data['rev_pct'] = (cat_data['revenue'] / total_system_revenue) * 100
                cat_data['pct_label'] = cat_data['rev_pct'].apply(lambda x: f"{x:.1f}%")
                cat_data['revenue_fmt'] = cat_data['revenue'].apply(lambda x: f"₹{format_indian_currency(x)}")

                # Create the bar chart
                bars = alt.Chart(cat_data).mark_bar().encode(
                    x=alt.X('category:N', title='Category', sort='-y'),
                    y=alt.Y('revenue:Q', title='Revenue (₹)'),
                    color=alt.Color('category:N', legend=None),
                    tooltip=[
                        alt.Tooltip('category:N', title='Category'),
                        alt.Tooltip('revenue_fmt:N', title='Revenue'),
                        alt.Tooltip('pct_label:N', title='Share %')
                    ]
                )

                # Create text labels for % share
                text = bars.mark_text(
                    align='center',
                    baseline='bottom',
                    dy=-10,
                    fontWeight='bold'
                ).encode(
                    text=alt.Text('pct_label:N')
                )

                st.altair_chart((bars + text).properties(height=500), use_container_width=True)
                st.caption(f"Labels show % of total system revenue (Total: ₹{format_indian_currency(total_system_revenue)})")
            else:
                st.info("No category revenue data available.")

        elif chart_to_show == "⏰ Hourly Revenue Analysis":
            st.markdown("**⏰ Hourly Revenue Analysis (Local Time - IST)**")
            
            # 1. Fetch hourly revenue and averages (Convert UTC to IST)
            cursor.execute("""
                WITH total_days AS (
                    SELECT COUNT(DISTINCT DATE(occurred_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')) as day_count
                    FROM orders
                    WHERE order_status = 'Success'
                ),
                hourly_stats AS (
                    SELECT 
                        EXTRACT(HOUR FROM (occurred_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')) as hour_num,
                        SUM(total) as revenue
                    FROM orders
                    WHERE order_status = 'Success'
                    GROUP BY hour_num
                )
                SELECT 
                    h.hour_num, 
                    h.revenue,
                    h.revenue / NULLIF(d.day_count, 0) as avg_revenue
                FROM hourly_stats h, total_days d
                ORDER BY CASE WHEN h.hour_num = 0 THEN 24 ELSE h.hour_num END
            """)
            hourly_data = pd.DataFrame(cursor.fetchall())
            
            if not hourly_data.empty:
                # Format labels
                def format_hour(h):
                    h = int(h)
                    if h == 0: return "12 AM"
                    if h == 12: return "12 PM"
                    if h < 12: return f"{h} AM"
                    return f"{h-12} PM"

                hourly_data['hour_label'] = hourly_data['hour_num'].apply(format_hour)
                hourly_data['revenue'] = hourly_data['revenue'].astype(float)
                hourly_data['avg_revenue'] = hourly_data['avg_revenue'].astype(float)
                
                total_daily_rev = hourly_data['revenue'].sum()
                if total_daily_rev > 0:
                    hourly_data['rev_pct'] = (hourly_data['revenue'] / total_daily_rev) * 100
                    hourly_data['pct_label'] = hourly_data['rev_pct'].apply(lambda x: f"{x:.1f}%")
                    hourly_data['revenue_fmt'] = hourly_data['revenue'].apply(lambda x: f"₹{format_indian_currency(x)}")
                    hourly_data['avg_revenue_fmt'] = hourly_data['avg_revenue'].apply(lambda x: f"₹{format_indian_currency(x)}")

                    # Sort for chart
                    hourly_data['sort_order'] = hourly_data['hour_num'].apply(lambda x: 24 if x == 0 else x)

                    bars = alt.Chart(hourly_data).mark_bar().encode(
                        x=alt.X('hour_label:N', title='Hour of Day', sort=alt.EncodingSortField(field='sort_order', order='ascending')),
                        y=alt.Y('revenue:Q', title='Revenue (₹)'),
                        color=alt.value('#FF4B4B'),
                        tooltip=[
                            alt.Tooltip('hour_label:N', title='Hour'),
                            alt.Tooltip('revenue_fmt:N', title='Total Revenue'),
                            alt.Tooltip('avg_revenue_fmt:N', title='Avg Revenue/Day'),
                            alt.Tooltip('pct_label:N', title='% of Total Sales')
                        ]
                    )
                    
                    # Add revenue labels above bars
                    text = bars.mark_text(
                        align='center',
                        baseline='bottom',
                        dy=-10,
                        fontWeight='bold'
                    ).encode(
                        text=alt.Text('revenue_fmt:N')
                    )

                    st.altair_chart((bars + text).properties(height=500), use_container_width=True)
                    st.caption(f"Hourly distribution of successful orders (IST). Total Analyzed: ₹{format_indian_currency(total_daily_rev)}")
                else:
                    st.info("No revenue recorded.")
            else:
                st.info("No hourly data available.")

        elif chart_to_show == "🛵 Order Source":
            st.markdown("**🛵 Order Source Analysis**")
            cursor.execute("""
                SELECT order_from, COUNT(*) as count, SUM(total) as revenue
                FROM orders
                WHERE order_status = 'Success'
                GROUP BY order_from
                ORDER BY count DESC
            """)
            source_data = pd.DataFrame(cursor.fetchall())
            if not source_data.empty:
                # Format revenue for labels
                def format_chart_value(val):
                    if val >= 10000000: # 1 Crore
                        return f"₹{val/10000000:.1f}Cr"
                    if val >= 100000: # 1 Lakh
                        return f"₹{val/100000:.1f}L"
                    return f"₹{format_indian_currency(val)}"

                source_data['revenue_float'] = source_data['revenue'].astype(float)
                source_data['revenue_label'] = source_data['revenue_float'].apply(format_chart_value)
                
                # Create the bar chart for counts
                bars = alt.Chart(source_data).mark_bar().encode(
                    x=alt.X('order_from:N', title='Source', sort='-y'),
                    y=alt.Y('count:Q', title='Orders'),
                    color=alt.Color('order_from:N', legend=None)
                )

                # Create text labels for revenue
                text = bars.mark_text(
                    align='center',
                    baseline='bottom',
                    dy=-10,
                    fontWeight='bold'
                ).encode(
                    text=alt.Text('revenue_label:N')
                )

                st.altair_chart((bars + text).properties(height=500), use_container_width=True)
            else:
                st.info("No order source data available.")
    
    cursor.close()


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
            # The API returns records strictly GREATER than the cursor,
            # so we should use the last processed ID directly.
            start_cursor = get_last_stream_id(conn)
        
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
            
    st.dataframe(
        df[cols], 
        use_container_width=True, 
        height=500,
        column_config=get_dataframe_config(df[cols])
    )


# Main App

# Sidebar
with st.sidebar:
    st.header("🧩 Modules")
    
    nav_modules = ["🏠 Insights", "🛒 Operations", "🍽️ Menu", "📦 Inventory & COGS", "🔍 SQL Query"]
    
    for mod in nav_modules:
        is_active = (st.session_state.get('active_module') == mod)
        if st.button(
            mod, 
            key=f"nav_{mod}", 
            use_container_width=True, 
            type="primary" if is_active else "secondary"
        ):
            st.session_state.active_module = mod
            st.rerun()
            
    module = st.session_state.active_module
    st.markdown("---")
    st.header("⚙️ Configuration")
    
    # Consolidated connection status
    status = st.session_state.get('db_status', 'Disconnected')
    
    if status == "Connected":
        st.markdown("🟢 **Connected**")
        
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
    elif status == "Connecting":
        st.markdown("🟡 **Connecting...**")
    else:
        st.markdown("🔴 **Failed to connect**")
        if st.button("🔄 Retry Connection"):
            get_db_connection(force_retry=True)
            st.rerun()

    st.markdown("---")
    
    # Database info
    if status == "Connected":
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
if st.session_state.get('db_status') != "Connected":
    st.info("👈 Please connect to the database using the sidebar")
    st.stop()

conn = get_db_connection()
if not conn:
    st.error("Failed to establish database connection")
    st.stop()

# Rendering is handled after sidebar definition

# Render based on module
if module == "🏠 Insights":
    render_insights_dashboard(conn)

elif module == "🛒 Operations":
    otab1, otab2, otab3, otab4, otab5, otab6 = st.tabs(["📦 Orders", "🛒 Order Items", "👥 Customers", "🍽️ Restaurants", "📊 Taxes", "💰 Discounts"])
    
    with otab1:
        st.header("Orders Table")
        render_datatable(
            conn, 
            'orders', 
            'created_on', 
            ['order_id', 'created_on', 'total', 'order_type', 'order_from', 'order_status', 'petpooja_order_id'],
            'orders',
            search_columns=['order_id', 'order_status', 'order_type', 'order_from']
        )

    with otab2:
        st.header("Order Items Table")
        render_datatable(
            conn, 
            'order_items', 
            'created_at', 
            ['order_item_id', 'order_id', 'created_at', 'total_price', 'quantity', 'name_raw', 'category_name', 'match_confidence'],
            'order_items',
            search_columns=['order_id', 'name_raw', 'category_name']
        )

    with otab3:
        st.header("Customers Table")
        render_datatable(
            conn, 
            'customers', 
            'last_order_date', 
            ['last_order_date', 'total_orders', 'total_spent', 'name', 'phone', 'first_order_date'],
            'customers',
            search_columns=['name', 'phone']
        )

    with otab4:
        st.header("Restaurants Table")
        render_datatable(
            conn, 
            'restaurants', 
            'restaurant_id', 
            ['restaurant_id', 'name', 'petpooja_restid'],
            'restaurants'
        )

    with otab5:
        st.header("Order Taxes Table")
        render_datatable(conn, 'order_taxes', 'order_tax_id', ['order_tax_id', 'tax_amount', 'tax_rate', 'tax_title', 'order_id'], 'taxes', search_columns=['tax_title', 'order_id'])

    with otab6:
        st.header("Order Discounts Table")
        render_datatable(conn, 'order_discounts', 'order_discount_id', ['order_discount_id', 'discount_amount', 'discount_rate', 'discount_title', 'order_id'], 'discounts', search_columns=['discount_title', 'order_id'])

elif module == "🍽️ Menu":
    mtab1, mtab2, mtab3, mtab4 = st.tabs(["📋 Menu Items", "📏 Variants", "🕸️ Menu Matrix", "✨ Resolutions"])
    
    with mtab1:
        # Merge Tool
        with st.expander("🛠️ Merge Menu Items", expanded=False):
            st.info("Merge a duplicate item (Source) into a canonical item (Target). The Source item will be DELETED and its stats/orders transferred to Target.")
            
            cursor = conn.cursor()
            cursor.execute("SELECT menu_item_id, name, type FROM menu_items ORDER BY name")
            all_items = cursor.fetchall()
            cursor.close()
            
            placeholder = (None, "-- Select Item --", "")
            merge_options = [placeholder] + all_items
            
            c1, c2 = st.columns(2)
            with c1:
                src_choice = st.selectbox("Source (To Delete)", 
                                        options=merge_options, 
                                        format_func=lambda x: f"{x[1]} ({x[2]})" if x[0] else x[1],
                                        key="merge_src_sel")
            with c2:
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

        # Menu Items Table
        st.header("Menu Items Table")
        render_datatable(
            conn, 
            'menu_items_summary_view', 
            'total_revenue', 
            ['total_revenue', 'total_sold', 'sold_as_item', 'sold_as_addon', 'name', 'type', 'is_active', 'menu_item_id'],
            'menu_items',
            search_columns=['menu_item_id', 'name', 'type', 'is_active']
        )

    with mtab2:
        st.header("Variants")
        render_datatable(
            conn, 
            'variants', 
            'variant_name', 
            ['variant_id', 'variant_name'],
            'variants'
        )

    with mtab3:
        st.header("Menu Matrix (Item x Variant)")
        
        # Remap Tool
        with st.expander("🔄 Remap Order Item"):
            raw_oid = st.text_input("Order Item ID (to move):")
            
            if raw_oid:
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
                    
                    cursor = conn.cursor()
                    cursor.execute("SELECT menu_item_id, name, type FROM menu_items ORDER BY name")
                    all_items_remap = cursor.fetchall()
                    
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
                                st.success(res['message']); st.rerun()
                            else:
                                st.error(res['message'])
                else:
                    st.warning("Order Item ID not found in cluster map")
        
        # Matrix Table
        query = """
            SELECT 
                mi.name, mi.type, v.variant_name, miv.price, miv.is_active, 
                miv.addon_eligible, miv.delivery_eligible 
            FROM menu_item_variants miv
            JOIN menu_items mi ON miv.menu_item_id = mi.menu_item_id
            JOIN variants v ON miv.variant_id = v.variant_id
            ORDER BY mi.type, mi.name, v.variant_name
        """
        with st.spinner("Loading matrix..."):
            df, error = execute_query(conn, query)
            if error: st.error(f"Error: {error}")
            else:
                st.info(f"Total combinations: {len(df)}")
                st.dataframe(
                    df, 
                    width="stretch", 
                    height=600,
                    column_config=get_dataframe_config(df)
                )

    with mtab4:
        render_resolutions_tab(conn)

elif module == "📦 Inventory & COGS":
    itab1, itab2 = st.tabs(["📦 Raw Materials", "💰 Finance & COGS"])
    with itab1:
        st.header("Inventory Management")
        st.info("Manage your stock levels, suppliers, and purchase orders.")
        st.warning("⚠️ Module under construction. Data tables will appear here.")
    with itab2:
        st.header("Finance & Profitability")
        st.info("Analyze Dish Costing (COGS), margins, and overall profitability.")
        st.warning("⚠️ Module under construction.")

elif module == "🔍 SQL Query":
    stab1 = st.tabs(["🔍 SQL Query"])[0]
    with stab1:
        st.header("SQL Query Interface")
        query = st.text_area("Enter SQL Query:", height=150, placeholder="SELECT * FROM orders LIMIT 10;")
        col1, col2 = st.columns([1, 4])
        with col1: limit = st.number_input("Limit Results", min_value=1, max_value=10000, value=1000, step=100)
        with col2: execute = st.button("▶️ Execute Query", type="primary", width="stretch")
        if execute and query:
            with st.spinner("Executing query..."):
                df, error = execute_query(conn, query, limit=limit if limit < 10000 else None)
                if error: st.error(f"Query Error: {error}")
                else:
                    st.success(f"✅ Query executed successfully. Returned {len(df)} rows.")
                    st.dataframe(
                        df, 
                        use_container_width=True, 
                        height=400,
                        column_config=get_dataframe_config(df)
                    )
