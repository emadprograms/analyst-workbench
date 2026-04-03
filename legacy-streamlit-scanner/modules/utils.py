import streamlit as st
from datetime import datetime, timezone

class AppLogger:
    def __init__(self, container):
        self.container = container
        self.log_messages = []

    def log(self, message: str):
        timestamp = datetime.now(timezone.utc).strftime('%H:%M:%S')
        new_msg = f"**{timestamp}Z:** {message}"
        self.log_messages.append(new_msg)
        if self.container:
            self.container.markdown("\n\n".join(self.log_messages[::-1]), unsafe_allow_html=True)

    def log_code(self, data, language='json'):
        timestamp = datetime.now(timezone.utc).strftime('%H:%M:%S')
        new_msg = f"**{timestamp}Z:** (See code block below)"
        self.log_messages.append(new_msg)
        if self.container:
            self.container.markdown("\n\n".join(self.log_messages[::-1]), unsafe_allow_html=True)
            if language == 'json' and isinstance(data, dict):
                self.container.json(data)
            else:
                self.container.code(str(data), language=language)

    def flush(self):
        if self.container:
            self.container.markdown("\n\n".join(self.log_messages[::-1]), unsafe_allow_html=True)

def get_turso_credentials():
    try:
        turso_secrets = st.secrets.get("turso", {})
        raw_db_url = turso_secrets.get("db_url")
        auth_token = turso_secrets.get("auth_token")

        if raw_db_url:
            db_url_https = raw_db_url.replace("libsql://", "https://")
            return db_url_https, auth_token
        else:
            return None, None
    except Exception as e:
        st.error(f"❌ Critical Initialization Error: {e}")
        return None, None
