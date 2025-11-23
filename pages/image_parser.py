import streamlit as st
import requests
import json
import base64
import time
import re
import os
from datetime import datetime
from PIL import Image
import pytesseract
from libsql_client import LibsqlError

# --- INTEGRATION IMPORTS ---
from modules.config import (
    KEY_MANAGER, 
    API_BASE_URL, 
    AVAILABLE_MODELS
)
from modules.db_utils import get_db_connection

# --- Session State Initialization ---
if 'logs' not in st.session_state:
    st.session_state.logs = []
if 'reset_counter' not in st.session_state:
    st.session_state.reset_counter = 0
if 'extraction_finished' not in st.session_state:
    st.session_state.extraction_finished = False
if 'final_text' not in st.session_state:
    st.session_state.final_text = ""

# --- Logger Setup ---
def log_message(message, level='INFO'):
    """Appends a formatted log message to the session state list."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    st.session_state.logs.append(f"{timestamp} - {level} - {message}")

# --- NEW: Robust API Caller (Adapts to Images) ---
def call_gemini_with_rotation(prompt, image_parts=None, model_name="gemini-2.0-flash", max_retries=5):
    """
    Makes a call to Gemini using the KeyManager rotation system.
    Handles Base64 encoding for images automatically.
    """
    if not KEY_MANAGER:
        log_message("KeyManager not initialized.", level='ERROR')
        return "ERROR: System Configuration Error."

    # Prepare Content Payload (Text + Optional Images)
    parts = [{"text": prompt}]
    
    if image_parts:
        # Convert raw bytes to Base64 for REST API
        for img in image_parts:
            try:
                b64_data = base64.b64encode(img['data']).decode('utf-8')
                parts.append({
                    "inline_data": {
                        "mime_type": img['mime_type'],
                        "data": b64_data
                    }
                })
            except Exception as e:
                log_message(f"Failed to encode image: {e}", level='ERROR')
                return f"ERROR: Image Encoding Failed - {e}"

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.2} # Low temp for OCR accuracy
    }
    headers = {'Content-Type': 'application/json'}

    # Rotation Loop
    for i in range(max_retries):
        current_api_key = None
        key_name = "Unknown"

        try:
            # 1. ACQUIRE KEY
            key_name, current_api_key, wait_time = KEY_MANAGER.get_key(target_model=model_name)

            if not current_api_key:
                log_message(f"⏳ All keys exhausted for {model_name}. Waiting {wait_time:.0f}s...", level='WARNING')
                if wait_time > 0 and i < max_retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    return f"ERROR: Global Rate Limit for {model_name}."

            log_message(f"🔑 Acquired '{key_name}' | Model: {model_name} (Attempt {i+1})")

            # 2. EXECUTE REQUEST
            url = f"{API_BASE_URL}/{model_name}:generateContent?key={current_api_key}"
            response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=90)

            # 3. REPORT STATUS
            if response.status_code == 200:
                KEY_MANAGER.report_success(current_api_key, model_id=model_name)
                
                try:
                    result = response.json()
                    return result["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError):
                    log_message(f"Invalid JSON response: {result}", level='ERROR')
                    KEY_MANAGER.report_failure(current_api_key, is_server_error=True)
                    continue # Retry

            elif response.status_code == 429:
                log_message(f"⛔ 429 Rate Limit on '{key_name}'.", level='WARNING')
                KEY_MANAGER.report_failure(current_api_key, is_server_error=False)
            
            elif response.status_code >= 500:
                log_message(f"☁️ Server Error {response.status_code} on '{key_name}'.", level='WARNING')
                KEY_MANAGER.report_failure(current_api_key, is_server_error=True)
            
            else:
                log_message(f"⚠️ API Error {response.status_code}: {response.text}", level='ERROR')
                KEY_MANAGER.report_failure(current_api_key, is_server_error=True)

        except Exception as e:
            log_message(f"💥 Exception using '{key_name}': {e}", level='ERROR')
            if current_api_key:
                KEY_MANAGER.report_failure(current_api_key, is_server_error=True)

        # Backoff
        if i < max_retries - 1:
            time.sleep(2 ** i)

    return "ERROR: Max Retries Exhausted."

# --- Streamlit App ---

st.set_page_config(page_title="AI Image Parser", layout="centered")

st.title("🖼️ AI Image to Text Converter")

# --- Validation ---
if not KEY_MANAGER:
    st.error("❌ Critical Error: KeyManager failed to initialize.")
    st.info("Check your database credentials in secrets.toml.")
    st.stop()

# --- System Status & Model Selection ---
col_status, col_model = st.columns([2, 1])
with col_status:
    st.info("✅ Gemini Rotation System: Active & Connected to Database")
with col_model:
    # Allow user to choose model (Flash is faster, Pro is better for complex layouts)
    selected_model = st.selectbox("Select AI Model", AVAILABLE_MODELS, index=0)

st.write(
    "Upload multiple scrolling screenshots (PNG, JPG, etc.). The AI will "
    "extract and combine the text into a single block."
)

def reset_app():
    """Increments a counter to reset the file_uploader and clears other state."""
    st.session_state.reset_counter += 1
    st.session_state.logs = []
    st.session_state.extraction_finished = False
    st.session_state.final_text = ""
    log_message("Application reset by user.")

uploaded_files = st.file_uploader(
    "Choose one or more images...",
    type=["png", "jpg", "jpeg", "bmp", "tiff"],
    accept_multiple_files=True,
    key=f"image_uploader_{st.session_state.reset_counter}"
)

if uploaded_files:
    st.subheader("Uploaded Images")
    for uploaded_file in uploaded_files:
        st.image(uploaded_file, caption=uploaded_file.name, use_container_width=True)

    tab_ai, tab_tesseract = st.tabs(["Parse using AI", "Parse using Pillow & Tesseract"])

    # --- TAB 1: AI EXTRACTION ---
    with tab_ai:
        st.header(f"AI Extraction ({selected_model})")
        st.write("Using multimodal AI to 'see' the image. Good for complex layouts.")
        
        if st.button("Extract and Combine Text with AI"):
            log_message(f"Starting AI extraction using {selected_model}")
            individual_texts = []
            has_error = False

            # 1. Extract per image
            with st.spinner("Step 1/2: Extracting text from each image..."):
                for i, uploaded_file in enumerate(uploaded_files):
                    log_message(f"Processing image {i+1}/{len(uploaded_files)}: {uploaded_file.name}")
                    
                    # Prepare Image Data
                    image_part = {
                        "mime_type": uploaded_file.type,
                        "data": uploaded_file.getvalue()
                    }
                    extract_prompt = "Extract all text from this image. Do not add any commentary or introductory text, just return the raw text."
                    
                    # CALL NEW API WRAPPER
                    extracted_text = call_gemini_with_rotation(
                        extract_prompt, 
                        image_parts=[image_part], 
                        model_name=selected_model
                    )

                    if extracted_text.startswith("ERROR:"):
                        st.error(f"Failed to process {uploaded_file.name}. {extracted_text}")
                        has_error = True
                        break
                    
                    individual_texts.append(extracted_text)

            # 2. Combine Results
            if not has_error and individual_texts:
                with st.spinner("Step 2/2: Combining text..."):
                    text_to_combine = ""
                    for i, text in enumerate(individual_texts):
                        text_to_combine += f"--- START IMAGE {i+1} ---\n{text}\n--- END IMAGE {i+1} ---\n\n"

                    combine_prompt = (
                        "You are an expert text editor. Merge these extracted text blocks into a single coherent document. "
                        "Remove the '--- START/END ---' markers. Preserve formatting. Return ONLY the final text.\n\n"
                        f"{text_to_combine}"
                    )

                    # CALL NEW API WRAPPER (Text only this time)
                    final_text = call_gemini_with_rotation(
                        combine_prompt, 
                        image_parts=None, 
                        model_name=selected_model
                    )

                    if final_text.startswith("ERROR:"):
                        st.error(f"Failed to combine. {final_text}")
                    else:
                        st.subheader("📄 Combined Extracted Text")
                        st.code(final_text, language=None)
                        st.session_state.final_text = final_text
                        st.session_state.extraction_finished = True

    # --- TAB 2: TESSERACT EXTRACTION ---
    with tab_tesseract:
        st.header("Tesseract OCR + AI Cleanup")
        st.write("Uses local OCR for speed, then AI to fix errors/combine.")
        
        if st.button("Extract with Tesseract"):
            log_message("Starting Tesseract extraction")
            individual_texts = []
            has_error = False

            # 1. Local OCR
            with st.spinner("Step 1/2: Running local OCR..."):
                for i, uploaded_file in enumerate(uploaded_files):
                    try:
                        image = Image.open(uploaded_file)
                        text = pytesseract.image_to_string(image, lang='eng')
                        individual_texts.append(text)
                    except Exception as e:
                        st.error(f"Tesseract Error on {uploaded_file.name}: {e}")
                        has_error = True
                        break
            
            # 2. AI Cleanup/Combine
            if not has_error and any(t.strip() for t in individual_texts):
                with st.spinner("Step 2/2: AI Cleanup & Combine..."):
                    text_to_combine = ""
                    for i, text in enumerate(individual_texts):
                        text_to_combine += f"--- START IMAGE {i+1} ---\n{text}\n--- END IMAGE {i+1} ---\n\n"

                    combine_prompt = (
                        "You are an expert editor. Merge these OCR text blocks. Fix OCR errors/typos. "
                        "Remove markers. Return ONLY the cleaned text.\n\n"
                        f"{text_to_combine}"
                    )

                    # CALL NEW API WRAPPER
                    final_text = call_gemini_with_rotation(
                        combine_prompt, 
                        image_parts=None, 
                        model_name=selected_model
                    )

                    if final_text.startswith("ERROR:"):
                        st.error(f"AI Failed. {final_text}")
                    else:
                        st.subheader("📄 Combined Text")
                        st.code(final_text, language=None)
                        st.session_state.final_text = final_text
                        st.session_state.extraction_finished = True

    # --- SAVE TO ARCHIVE (Now using Turso) ---
    if st.session_state.final_text:
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            save_date = st.date_input("Select Date", value=datetime.now())
        with col2:
            category = st.selectbox("Select Category", ["Market Open Briefing", "Market Close Summary", "Other..."])

        custom_category = ""
        if category == "Other...":
            custom_category = st.text_input("Custom Category Name")

        if st.button("💾 Save Text to Database Archive", use_container_width=True):
            final_cat = custom_category if category == "Other..." and custom_category else category
            
            if final_cat and final_cat != "Other...":
                # Logic to sanitize category name
                is_news = category in ["Market Open Briefing", "Market Close Summary"] or (category == "Other..." and custom_category)
                if is_news:
                    clean_cat = re.sub(r'[^\w\-]', '_', final_cat.replace(' ', '-'))
                    db_category = f"news_{clean_cat}"
                else:
                    db_category = final_cat

                # Turso Save
                conn = None
                try:
                    conn = get_db_connection()
                    # Note: 'data_archive' table is created in setup_db.py now.
                    # We use UPSERT logic compatible with SQLite/LibSQL
                    conn.execute(
                        """
                        INSERT INTO data_archive (date, ticker, raw_text_summary)
                        VALUES (?, ?, ?)
                        ON CONFLICT(date, ticker) DO UPDATE SET
                            raw_text_summary = excluded.raw_text_summary
                        """,
                        (save_date.strftime('%Y-%m-%d'), db_category, st.session_state.final_text)
                    )
                    # No commit needed for Turso client
                    st.success(f"✅ Saved to Database: '{final_cat}'")
                except LibsqlError as e:
                    st.error(f"Database Error: {e}")
                except Exception as e:
                    st.error(f"An unexpected error occurred: {e}")
                finally:
                    if conn: conn.close()
            else:
                st.warning("Invalid Category.")

    st.divider()
    if st.session_state.get('extraction_finished', False):
        st.button("Start Over", on_click=reset_app, use_container_width=True)

else:
    st.info("Please upload images to begin.")

# --- Log Display ---
with st.expander("View System Logs"):
    st.code("\n".join(st.session_state.logs[::-1]), language='log')
    if st.button("Clear Logs"):
        st.session_state.logs = []
        st.rerun()