import streamlit as st
from src.core.queries.table_queries import execute_raw_query

def render_sql_console_page(conn):
    st.header("SQL Query Interface")
    query = st.text_area("Enter SQL Query:", height=150, placeholder="SELECT * FROM orders LIMIT 10;")
    col1, col2 = st.columns([1, 4])
    with col1: limit = st.number_input("Limit Results", min_value=1, max_value=10000, value=1000, step=100)
    with col2: execute = st.button("▶️ Execute Query", type="primary", width="stretch")
    if execute and query:
        with st.spinner("Executing query..."):
            df, error = execute_raw_query(conn, query, limit=limit if limit < 10000 else None)
            if error: st.error(f"Query Error: {error}")
            else:
                st.success(f"✅ Query executed successfully. Returned {len(df)} rows.")
                st.dataframe(
                    df, 
                    use_container_width=True, 
                    height=400
                )
