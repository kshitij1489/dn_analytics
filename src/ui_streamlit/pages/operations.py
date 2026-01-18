import streamlit as st
from src.ui_streamlit.components.datatable import render_datatable

def render_operations_page(conn):
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
