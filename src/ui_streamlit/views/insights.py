import streamlit as st
import pandas as pd
import altair as alt
from src.core.config.constants import INDIAN_HOLIDAYS
from src.core.utils.formatting import format_indian_currency, format_chart_value
from src.core.queries.insights_queries import fetch_kpis
from src.core.queries.menu_queries import fetch_menu_stats, fetch_menu_types
from src.core.queries.customer_queries import fetch_customer_reorder_rate, fetch_customer_loyalty, fetch_top_customers

def render_insights_dashboard(conn):
    st.header("🏠 Executive Insights")
    
    # 1. KPIs
    kpis = fetch_kpis(conn)
    if kpis:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Revenue", f"₹{format_indian_currency(kpis['total_revenue'])}")
        c2.metric("Orders", f"{kpis['total_orders']:,}")
        c3.metric("Avg Order", f"₹{format_indian_currency(kpis['avg_order_value'])}")
        c4.metric("Customers", f"{kpis['total_customers']:,}")
    
    st.markdown("---")
    
    # 2. Insights Tabs
    tab_ds, tab_menu, tab_cust, tab_charts = st.tabs(["Daily Sales", "Menu Items", "Customer", "Charts"])
    
    with tab_ds:
        st.subheader("🗓️ Daily Sales Performance")
        
        # We need to replicate the query execution here for now as `insights_queries.py` didn't include
        # the exact formatting needed for `st.dataframe` config, or we import the query.
        # Wait, I extracted `fetch_daily_sales` in `insights_queries.py`.
        from src.core.queries.insights_queries import fetch_daily_sales
        ds_data = fetch_daily_sales(conn)
        
        if not ds_data.empty:
            st.dataframe(
                ds_data, 
                use_container_width=True, 
                height=600,
                column_config={
                    "order_date": "Date",
                    "total_revenue": st.column_config.NumberColumn("Total Revenue", format="₹%d"),
                    "net_revenue": st.column_config.NumberColumn("Net Revenue", format="₹%d"),
                    "tax_collected": st.column_config.NumberColumn("Tax Collected", format="₹%d"),
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
        st.subheader("🗓️ Menu Item Performance")
        
        # 1. Date & Day Filters
        c_date1, c_date2, c_days = st.columns([1, 1, 3])
        
        with c_date1:
            start_date = st.date_input("Begin Date:", value=pd.to_datetime('2024-01-01'), key="menu_start_date")
        with c_date2:
            end_date = st.date_input("End Date:", value=pd.to_datetime('today'), key="menu_end_date")
            
        with c_days:
            st.write("Include Days:")
            days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            days_abbr = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            
            if 'menu_selected_weekdays' not in st.session_state:
                st.session_state.menu_selected_weekdays = days_of_week.copy()
                
            weekday_cols = st.columns(7)
            for i, day in enumerate(days_of_week):
                is_selected = day in st.session_state.menu_selected_weekdays
                if weekday_cols[i].button(
                    days_abbr[i], 
                    key=f"menu_btn_day_{day}", 
                    use_container_width=True,
                    type="primary" if is_selected else "secondary"
                ):
                    if is_selected:
                        st.session_state.menu_selected_weekdays.remove(day)
                    else:
                        st.session_state.menu_selected_weekdays.append(day)
                    st.rerun()

        st.markdown("---")
        
        # 2. Search & Type Filters
        c_search, c_type = st.columns(2)
        with c_search:
            name_search = st.text_input("Search by Item Name:", key="menu_name_filter", placeholder="e.g. Chocolate")
        with c_type:
            types = fetch_menu_types(conn)
            type_choice = st.selectbox("Filter by Type:", ["All"] + types, key="menu_type_filter")

        # 3. Data Fetching
        menu_data = fetch_menu_stats(
            conn, 
            name_search, 
            type_choice, 
            start_date=start_date, 
            end_date=end_date, 
            selected_weekdays=st.session_state.menu_selected_weekdays
        )
        
        if not menu_data.empty:
            st.dataframe(
                menu_data, 
                use_container_width=True, 
                height=600,
                column_config={
                    "Total Revenue": st.column_config.NumberColumn(format="₹%d"),
                    "Repeat Customers": st.column_config.NumberColumn(format="%d"),
                    "Unique Customers": st.column_config.NumberColumn(format="%d"),
                    "Total Sold (Qty)": st.column_config.NumberColumn("Total Sold", format="%d"),
                    "As Item (Qty)": st.column_config.NumberColumn("As Item", format="%d"),
                    "As Addon (Qty)": st.column_config.NumberColumn("As Addon", format="%d"),
                    "Reorder Count": st.column_config.NumberColumn(format="%d")
                }
            )
        else:
            st.info("No menu analytics found for current filters.")
        
    with tab_cust:
        reorder = fetch_customer_reorder_rate(conn)
        
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
        
        if "customer_view" not in st.session_state:
            st.session_state.customer_view = "loyalty"
            
        cv_col1, cv_col2, _ = st.columns([1, 1, 2])
        with cv_col1:
            if st.button("🔄 Customer Retention", use_container_width=True, type="primary" if st.session_state.customer_view == "loyalty" else "secondary"):
                st.session_state.customer_view = "loyalty"
                st.rerun()
        with cv_col2:
            if st.button("💎 Top Verified Customers", use_container_width=True, type="primary" if st.session_state.customer_view == "top_spend" else "secondary"):
                st.session_state.customer_view = "top_spend"
                st.rerun()
        
        st.markdown("---")
        
        if st.session_state.customer_view == "loyalty":
            loyalty_df = fetch_customer_loyalty(conn)
            
            if not loyalty_df.empty:
                display_cols = [c for c in loyalty_df.columns if c != 'month_sort']
                st.dataframe(
                    loyalty_df[display_cols],
                    use_container_width=True,
                    height=500,
                    column_config={
                        "Total Revenue": st.column_config.NumberColumn(format="₹%d"),
                        "Repeat Revenue": st.column_config.NumberColumn(format="₹%d"),
                        "Order Repeat%": st.column_config.NumberColumn(format="%.1f%%"),
                        "Repeat Customer %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Revenue Repeat %": st.column_config.NumberColumn(format="%.1f%%")
                    }
                )
            else:
                st.info("No loyalty data found yet.")
        
        elif st.session_state.customer_view == "top_spend":
            top_cust = fetch_top_customers(conn)
            
            if not top_cust.empty:
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
        # Import new query functions
        from src.core.queries.insights_queries import (
            fetch_sales_trend, 
            fetch_category_trend, 
            fetch_hourly_revenue_data, 
            fetch_top_items_data,
            fetch_revenue_by_category_data,
            fetch_order_source_data
        )

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
                        key=f"chart_agg_metric_{chart_to_show}"
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
                    key=f"btn_day_{day}_{chart_to_show}_v2", 
                    use_container_width=True,
                    type="primary" if is_selected else "secondary"
                ):
                    if is_selected:
                        st.session_state.selected_weekdays.remove(day)
                    else:
                        st.session_state.selected_weekdays.append(day)
                    st.rerun()

            # Fetch Data
            if chart_to_show == "📉 Sales by Category Trend":
                chart_data = fetch_category_trend(conn)
            else:
                chart_data = fetch_sales_trend(conn)
            
            if not chart_data.empty:
                chart_data['date'] = pd.to_datetime(chart_data['date'])
                chart_data['revenue'] = chart_data['revenue'].astype(float)
                if 'num_orders' in chart_data.columns:
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
                        # We need to group by category and then resample
                        plot_list = []
                        # filtered_df index is date, columns: category, revenue, day_name
                        for cat, group in filtered_df.groupby('category'):
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

                    elif chart_to_show == "🖇️ Revenue vs Orders":
                        st.markdown("**🖇️ Revenue vs Order Volume**")
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
            
            top_items, total_system_revenue = fetch_top_items_data(conn)
            
            if not top_items.empty and total_system_revenue > 0:
                top_items['item_revenue'] = top_items['item_revenue'].fillna(0).astype(float)
                top_items['rev_pct'] = (top_items['item_revenue'] / total_system_revenue) * 100
                top_items['pct_label'] = top_items['rev_pct'].apply(lambda x: f"{x:.1f}%")

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
            
            cat_data, total_system_revenue = fetch_revenue_by_category_data(conn)
            
            if not cat_data.empty and total_system_revenue > 0:
                cat_data['revenue'] = cat_data['revenue'].astype(float)
                cat_data['rev_pct'] = (cat_data['revenue'] / total_system_revenue) * 100
                cat_data['pct_label'] = cat_data['rev_pct'].apply(lambda x: f"{x:.1f}%")
                cat_data['revenue_fmt'] = cat_data['revenue'].apply(lambda x: f"₹{format_indian_currency(x)}")

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
            
            hourly_data = fetch_hourly_revenue_data(conn)
            
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
            source_data = fetch_order_source_data(conn)
            
            if not source_data.empty:
                source_data['revenue_float'] = source_data['revenue'].astype(float)
                source_data['revenue_label'] = source_data['revenue_float'].apply(format_chart_value)
                
                bars = alt.Chart(source_data).mark_bar().encode(
                    x=alt.X('order_from:N', title='Source', sort='-y'),
                    y=alt.Y('count:Q', title='Orders'),
                    color=alt.Color('order_from:N', legend=None)
                )

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
