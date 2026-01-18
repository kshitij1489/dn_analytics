import streamlit as st
import os
from pathlib import Path
from src.core.queries.table_queries import execute_raw_query

def render_sql_console_page(conn):
    st.header("SQL Query Interface")
    
    # Initialize view state
    if "sql_view" not in st.session_state:
        st.session_state.sql_view = "query"
    
    # View selection buttons
    col_btn1, col_btn2, _ = st.columns([1, 1, 4])
    with col_btn1:
        if st.button("📊 SQL Query", use_container_width=True, type="primary" if st.session_state.sql_view == "query" else "secondary"):
            st.session_state.sql_view = "query"
            st.rerun()
    with col_btn2:
        if st.button("🤖 LLM Prompt", use_container_width=True, type="primary" if st.session_state.sql_view == "prompt" else "secondary"):
            st.session_state.sql_view = "prompt"
            st.rerun()
    
    st.markdown("---")
    
    if st.session_state.sql_view == "query":
        render_query_view(conn)
    else:
        render_prompt_view()

def render_query_view(conn):
    query = st.text_area("Enter SQL Query:", height=150, placeholder="SELECT * FROM orders LIMIT 10;")
    col1, col2 = st.columns([1, 4])
    with col1: 
        limit = st.number_input("Limit Results", min_value=1, max_value=10000, value=1000, step=100)
    with col2: 
        execute = st.button("▶️ Execute Query", type="primary", use_container_width=True)
    
    if execute and query:
        with st.spinner("Executing query..."):
            df, error = execute_raw_query(conn, query, limit=limit if limit < 10000 else None)
            if error: 
                st.error(f"Query Error: {error}")
            else:
                st.success(f"✅ Query executed successfully. Returned {len(df)} rows.")
                st.dataframe(
                    df, 
                    use_container_width=True, 
                    height=400
                )

def render_prompt_view():
    st.subheader("Gemini System Prompt")
    st.info("Copy the block below and paste it into the Gemini website to provide full database context.")
    
    # Path to the generated prompt
    # Use a relative path from the project root to ensure it works in both local and Docker environments
    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    prompt_path = base_dir / "database" / "gemini_context_prompt.md"
    
    try:
        if os.path.exists(prompt_path):
            with open(prompt_path, "r") as f:
                prompt_content = f.read()
            
            st.code(prompt_content, language="markdown")
            
            # Button for easy copying (built-in in st.code usually, but we can add a text area too)
            st.text_area("Plain Text (for easy copying):", value=prompt_content, height=300)
        else:
            st.warning("LLM Prompt file not found. Please ensure `gemini_context_prompt.md` exists in the brain directory.")
    except Exception as e:
        st.error(f"Error reading prompt file: {e}")
