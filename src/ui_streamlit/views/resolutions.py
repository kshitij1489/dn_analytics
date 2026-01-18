import streamlit as st
from scripts.resolve_unclustered import get_unverified_items, verify_item
from utils.menu_utils import merge_menu_items, resolve_item_rename

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
