import streamlit as st

st.set_page_config(layout="wide")
st.title("Secrets Check")

st.info("This page will show all secrets Streamlit has loaded.")

# This command prints ALL secrets Streamlit can find.
st.write(st.secrets.to_dict())