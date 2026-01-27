import streamlit as st

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
