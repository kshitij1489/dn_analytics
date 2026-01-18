import streamlit as st
import pandas as pd
from src.core.db.connection import get_db_connection
from src.core.services.sync_service import sync_database

def render_sidebar():
    """Renders the sidebar and returns the selected module name"""
    with st.sidebar:
        st.header("🧩 Modules")
        
        nav_modules = ["🏠 Insights", "🛒 Operations", "🍽️ Menu", "📦 Inventory & COGS", "🔍 SQL Query"]
        
        # Ensure active_module is in session state
        if 'active_module' not in st.session_state:
            st.session_state.active_module = nav_modules[0]
            
        for mod in nav_modules:
            is_active = (st.session_state.active_module == mod)
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
                conn, _ = get_db_connection() # Should assume valid if status is Connected
                if conn:
                    # New Service-based Sync Logic
                    status_placeholder = st.empty()
                    progress_bar = st.progress(0)
                    
                    final_result = None
                    try:
                        for status_event in sync_database(conn, st.session_state.get('item_cluster')):
                            if status_event.type == 'info':
                                status_placeholder.info(status_event.message)
                            elif status_event.type == 'progress':
                                status_placeholder.text(status_event.message)
                                progress_bar.progress(status_event.progress)
                            elif status_event.type == 'done':
                                final_result = status_event
                                progress_bar.empty()
                                status_placeholder.empty()
                            elif status_event.type == 'error':
                                st.error(status_event.message)
                        
                        if final_result:
                            # Update global version to trigger refreshes
                            if final_result.total > 0:
                                st.session_state.data_version += 1
                                st.success(f"✅ Synced {final_result.total} new orders")
                                if final_result.stats:
                                    st.json(final_result.stats)
                            else:
                                st.info(final_result.message)
                                
                    except Exception as e:
                        st.error(f"Sync failed: {e}")
                            
        elif status == "Connecting":
            st.markdown("🟡 **Connecting...**")
        else:
            st.markdown("🔴 **Failed to connect**")
            # Connection logic is typically automatic on start, but we can offer retry
            if st.button("🔄 Retry Connection"):
                # We can trigger a re-check by setting status to connecting
                # The main app loop handles the actual connection attempt
                st.session_state.db_status = "Connecting" 
                st.rerun()
    
        st.markdown("---")
        
        # Database info
        if status == "Connected":
            st.header("📈 Database Stats")
            try:
                conn, _ = get_db_connection()
                if conn:
                    # Quick stats query
                    stats_query = """
                        SELECT 
                            (SELECT COUNT(*) FROM orders) as orders,
                            (SELECT COUNT(*) FROM order_items) as order_items,
                            (SELECT COUNT(*) FROM customers) as customers,
                            (SELECT MAX(created_on) FROM orders) as last_order
                    """
                    # We can use pd.read_sql or cursor. 
                    # For quickness in UI component, pd is easy
                    stats_df = pd.read_sql_query(stats_query, conn)
                    if not stats_df.empty:
                        stats = stats_df.iloc[0]
                        st.metric("Orders", f"{stats['orders']:,}")
                        st.metric("Order Items", f"{stats['order_items']:,}")
                        st.metric("Customers", f"{stats['customers']:,}")
                        if stats['last_order']:
                            st.caption(f"Last Order: {str(stats['last_order'])}")
            except Exception as e:
                st.warning(f"Stats unavailable: {e}")
                
    return module
