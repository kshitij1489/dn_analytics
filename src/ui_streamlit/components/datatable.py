import streamlit as st
import pandas as pd
from src.core.queries.table_queries import fetch_paginated_table
from src.ui_streamlit.utils import get_dataframe_config

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
            "Sort by", 
            sort_columns, 
            index=default_index,
            key=f"{page_key_prefix}_sort_col"
        )
    with col2:
        sort_dir = st.selectbox(
            "Direction", 
            ["DESC", "ASC"], 
            key=f"{page_key_prefix}_sort_dir"
        )
    with col3:
        st.write("") # Spacer
        if st.button("Reset Filters", key=f"{page_key_prefix}_reset", on_click=reset_filters):
            pass
    
    # 3. Fetch Data
    page_size = 50
    if f"{page_key_prefix}_page" not in st.session_state:
        st.session_state[f"{page_key_prefix}_page"] = 1
        
    current_page = st.session_state[f"{page_key_prefix}_page"]
    
    with st.spinner("Fetching data..."):
        df, total_count, error = fetch_paginated_table(
            conn, 
            table_name, 
            page=current_page, 
            page_size=page_size, 
            sort_column=sort_col, 
            sort_direction=sort_dir,
            filters=filters
        )
    
    if error:
        st.error(f"Error fetching data: {error}")
        return

    # 4. Render Table
    st.markdown(f"**Showing {len(df)} records (Total: {total_count:,})**")
    
    # Use Container Width for better layout
    df_kwargs = {
        "use_container_width": True,
        "column_config": get_dataframe_config(df)
    }
    if len(df) > 10:
        df_kwargs["height"] = 600
        
    st.dataframe(df, **df_kwargs)
    
    # 5. Pagination
    total_pages = (total_count // page_size) + (1 if total_count % page_size > 0 else 0)
    
    if total_pages > 1:
        c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 2, 1])
        with c2:
            if st.button("Previous", disabled=current_page <= 1, key=f"{page_key_prefix}_prev"):
                st.session_state[f"{page_key_prefix}_page"] -= 1
                st.rerun()
        with c3:
            st.markdown(f"<div style='text-align: center; padding-top: 5px'>Page {current_page} of {total_pages}</div>", unsafe_allow_html=True)
        with c4:
            if st.button("Next", disabled=current_page >= total_pages, key=f"{page_key_prefix}_next"):
                st.session_state[f"{page_key_prefix}_page"] += 1
                st.rerun()
