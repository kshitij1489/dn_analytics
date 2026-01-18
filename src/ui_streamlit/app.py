import streamlit as st
import sys
import os
from pathlib import Path

# Add project root to sys.path to ensure absolute imports from src works
# This handles the case where simply running `streamlit run src/ui_streamlit/app.py` 
# might not have the root in path.
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Import Core & UI
from src.core.db.connection import get_db_connection
from src.core.db.schema import check_schema_exists
from src.core.config.constants import INDIAN_HOLIDAYS
from src.ui_streamlit.sidebar import render_sidebar
from src.ui_streamlit.pages.insights import render_insights_dashboard
from src.ui_streamlit.pages.menu import render_menu_page
from src.ui_streamlit.pages.operations import render_operations_page
from src.ui_streamlit.pages.sql_console import render_sql_console_page
from services.clustering_service import OrderItemCluster
from database.load_orders import create_schema_if_needed
from scripts.seed_from_backups import perform_seeding

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="Analytics Database Client",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLES ---
st.markdown("""
<style>
    /* Premium styling for navigation buttons */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(-n+8) button[kind="primary"] {
        background-color: #BAE6FD !important;
        color: #0369A1 !important;
        border: 1px solid #7DD3FC !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1) !important;
        font-weight: 600 !important;
        border-radius: 8px !important;
    }
</style>
""", unsafe_allow_html=True)

# --- STATE INIT ---
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
    st.session_state.db_status = "Disconnected"
if 'active_module' not in st.session_state:
    st.session_state.active_module = "🏠 Insights"

# --- INITIALIZATION LOGIC ---
def initialize_app():
    """Initialize database connection and services automatically"""
    if st.session_state.initialized:
        return
        
    conn, status = get_db_connection()
    if conn:
        st.session_state.db_conn = conn
        try:
            # 0. Check Schema
            if not check_schema_exists(conn):
                 create_schema_if_needed(conn)
            
            # 1. Auto-seed if empty
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM menu_items")
            count = cursor.fetchone()[0]
            cursor.close()
            
            if count == 0:
                perform_seeding(conn)
            
            # 2. Initialize cluster (Keep in session state for now as it caches matches)
            if st.session_state.item_cluster is None:
                st.session_state.item_cluster = OrderItemCluster(conn)
            
            st.session_state.db_status = "Connected"
            st.session_state.initialized = True
            
        except Exception as e:
            st.session_state.db_status = "Failed"
            st.error(f"Initialization failed: {e}")
    else:
        st.session_state.db_status = "Failed"

# Run Init
initialize_app()

# --- MAIN RENDER ---
module = render_sidebar()

# Get fresh connection for page rendering if needed, or use session one
# We generally rely on the one established or create new one per request if stateless preferences.
# db/connection.py creates new connections easily.
# For efficiency, we can reuse `st.session_state.db_conn` if it's open.
conn = st.session_state.db_conn
if not conn or conn.closed:
    conn, _ = get_db_connection()
    st.session_state.db_conn = conn

if not conn:
    st.info("👈 Please connect to the database using the sidebar")
    st.stop()

# Route to Page
if module == "🏠 Insights":
    render_insights_dashboard(conn)
elif module == "🛒 Operations":
    render_operations_page(conn)
elif module == "🍽️ Menu":
    render_menu_page(conn)
elif module == "📦 Inventory & COGS":
    st.title(module)
    st.info("Coming soon")
elif module == "🔍 SQL Query":
    render_sql_console_page(conn)
