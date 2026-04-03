import streamlit as st

st.set_page_config(page_title="Premarket Scanner", page_icon="👋")

st.write("# Welcome to Premarket Scanner! 👋")

st.sidebar.success("Select a demo above.")

st.markdown(
    """
    This is the main entry point. 
    
    Please check the **Pages** in the sidebar to access:
    1. **Context Engine** - The full application.
    2. **Engine Lab** - The isolated testing workbench.
    """
)