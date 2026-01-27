import streamlit as st
from psycopg2.extras import RealDictCursor
from utils.menu_utils import merge_menu_items, remap_order_item_cluster, undo_merge
from src.ui_streamlit.components.datatable import render_datatable
from src.ui_streamlit.views.resolutions import render_resolutions_tab
from src.core.queries.table_queries import execute_raw_query
from src.ui_streamlit.utils import get_dataframe_config

# Note: get_dataframe_config was not extracted to core/utils/formatting.py in my previous step! 
# I missed it. I need to fix that or define it here or import it if I put it elsewhere.
# In step 91 I created formatting.py but only with format_indian_currency, format_hour, format_chart_value.
# get_dataframe_config uses st.column_config so it belongs in UI utils or components.
# I'll create `src/ui_streamlit/utils.py` or just define it in `src/ui_streamlit/components/datatable.py`?
# Plan said: `src/ui_streamlit/formatting.py` (or similar).
# I will define it in `src/ui_streamlit/utils.py` and create that file after this.

def render_menu_page(conn):
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
            df, error = execute_raw_query(conn, query)
            if error: st.error(f"Error: {error}")
            else:
                st.info(f"Total combinations: {len(df)}")
                # We need get_dataframe_config here too
                st.dataframe(df, width=1000, height=600)

    with mtab4:
        render_resolutions_tab(conn)
