import streamlit as st
import pandas as pd
import re
import math
import hashlib
import requests
from io import BytesIO
from PIL import Image as PILImage
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, 
    Image as RLImage, KeepInFrame, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A3
from reportlab.lib import colors
from reportlab.lib.units import inch
import tempfile
import os
from datetime import datetime, timedelta
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from pathlib import Path
from bs4 import BeautifulSoup

# ========== Page Config ==========
st.set_page_config(page_title="Quotation Builder", page_icon="🪑", layout="wide")

# ========== User Credentials ==========
def init_session_state():
    """Initialize session state variables"""
    defaults = {
        "logged_in": False,
        "user_email": None,
        "role": None,
        "form_submitted": False,
        "company_details": {},
        "rows": 1,
        "row_indices": [0],
        "selected_products": {},
        "sheet_data": None,
        "last_sheet_update": 0,
        "terms_and_conditions": {
            "value": """1. Prices are in Saudi Riyal (SAR).
            2. Prices include 14% Value Added Tax (VAT), calculated separately.
            3. Prices also cover delivery, installation, and assembly.
            4. Financial Offer Validity: 30 days from the submission date.
            5. Delivery period: 21 to 30 days from the issuance of a purchase order (PO), advance payment and selection of preferred colors.
            6. Goods will be stored free of charge in the company's warehouse for 7 days from the final delivery date. However, an additional fee of 1% of the total order value, up to a maximum of 5%, will be added weekly thereafter.
            7. Delivery Locations: Unit No.4, Building No. 2981, Al Ihsaa st., Ar Rabwa, Riyadh, KSA 12813
            8. Our technical offer fully complies with the requested product specifications.
            9. Warranty: All products are covered by a 12-month warranty starting from the final delivery and installation date, guaranteeing against manufacturer defects, parts failure due to installation errors, and missing or incorrect parts.
            10. Maintenance service and maximum response time: will be within 48 - 72 hours from the notification time via email.
            11. Terms of payment: 50% down payment and 50% upon confirmation of successful completion, handover of goods, original invoice, and delivery note to headquarters."""
        },
        "history": [],  
        "history_loaded": False,         
        "pdf_data": [],          
        "cart": [],              
        "edit_mode": False,      
    }
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value
init_session_state()

@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_users_from_sheet():
    """Load user credentials from Google Sheet by name (not ID)"""
    try:
        gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
        # 🔓 Open by spreadsheet name (must be shared with service account email)
        sh = gc.open("Amjad's users")  # ← Spreadsheet name
        worksheet = sh.sheet1  # Assumes user data is in first sheet
        rows = worksheet.get_all_values()
        if not rows:
            st.error("❌ User sheet is empty.")
            st.stop()
        headers = [h.strip() for h in rows[0]]
        data = []
        for row in rows[1:]:
            if len(row) < len(headers):
                row += [""] * (len(headers) - len(row))
            data.append(dict(zip(headers, row)))
        users = {}
        for row in data:
            name = str(row.get("Name", "")).strip()
            email = str(row.get("Email", "")).strip().lower()
            password = str(row.get("Password", "")).strip()
            role = str(row.get("Role", "")).strip()
            if "@" not in email:
                st.warning(f"⚠ Invalid email format: {email}")
                continue
            username = email.split("@")[0]
            users[email] = {
                "username": username,
                "full_name": name,        
                "password": password,
                "role": role
            }
        if not users:
            st.error("❌ No valid users found in 'Amjad's users' sheet.")
            st.stop()
        st.success("✅ Users loaded successfully!")
        return users
    except gspread.SpreadsheetNotFound:
        st.error("❌ Spreadsheet 'Amjad's users' not found.")
        st.info("💡 Make sure:")
        st.markdown("""
        - The spreadsheet is named exactly: Amjad's users  
        - It is shared with: amjadquotation@quotationappamjad.iam.gserviceaccount.com  
        - The service account has Editor access
        """)
        st.stop()
    except Exception as e:
        st.error(f"❌ Unexpected error loading users: {e}")
        st.stop()

# Load users
USERS = load_users_from_sheet()


@st.cache_resource
def get_company_sheet():
    try:
        gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
        sh = gc.open("clients Db")
        return sh.sheet1
    except gspread.SpreadsheetNotFound:
        st.error("❌ Spreadsheet 'Company Details' not found.")
        st.info("💡 Make sure the sheet is shared with: amjadquotation@quotationappamjad.iam.gserviceaccount.com")
        return None
    except Exception as e:
        st.error(f"❌ Failed to connect to company sheet: {e}")
        return None
    

def load_company_data(sheet):
    """Load company data from Google Sheet into a list of dicts"""
    if sheet is None:
        return []
    try:
        df = get_as_dataframe(sheet)
        df.dropna(how='all', inplace=True)  # Remove completely empty rows
        # Rename columns to match your form keys
        column_mapping = {
            'Company': 'company_name',
            'Contact person': 'contact_person',
            'Contact Email': 'contact_email',
            'Phone number': 'contact_phone',
            'Address': 'address'
        }
        df.rename(columns=column_mapping, inplace=True)
        
        # Replace NaN values with empty strings
        df = df.fillna("")
        
        if 'contact_phone' in df.columns:
            def clean_phone(x):
                if not x:
                    return ""
                try:
                    num = float(x)
                    if num.is_integer():
                        return str(int(num))
                except (ValueError, TypeError):
                    pass
                return str(x)
            
            df['contact_phone'] = df['contact_phone'].apply(clean_phone)
        
        return df.to_dict(orient='records')
    except Exception as e:
        st.error(f"❌ Failed to load company data: {e}")
        return []


def save_company_to_sheet(sheet, company_data):
    """Append new company data to the company sheet"""
    if sheet is None:
        st.warning("⚠ Could not save: Company sheet not available.")
        return False
    try:
        phone = company_data.get("contact_phone", "")
        if phone:
            try:
                num = float(phone)
                if num.is_integer():
                    phone = str(int(num))
                else:
                    phone = str(num)
            except (ValueError, TypeError):
                phone = str(phone)
        
        row = [
            company_data.get("company_name", ""),
            company_data.get("contact_person", ""),
            company_data.get("contact_email", ""),
            phone,
            company_data.get("address", "")
        ]
        sheet.append_row(row)
        st.success(f"✅ Company '{company_data['company_name']}' saved to sheet!")
        return True
    except Exception as e:
        st.error(f"❌ Failed to save company: {e}")
        return False
# ========== Connect to Quotation History Sheet ==========
@st.cache_resource
def get_history_sheet():
    # Cache the connection to the history Google Sheet
    # Returns the first worksheet of "Amjad's history" spreadsheet
    try:
        gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
    
        sh = gc.open("Amjad's history")  
        return sh.sheet1
        
    except gspread.SpreadsheetNotFound:
        # Handle case where spreadsheet is not found
        st.error("❌ Spreadsheet 'Amjad's history' not found.")
        st.info("💡 Make sure:")
        st.markdown("""
        - The spreadsheet is named exactly: Amjad's history  
        - It is shared with: amjadquotation@quotationappamjad.iam.gserviceaccount.com  
        - The service account has Editor access
        """)
        return None
        
    except Exception as e:
        # Handle any other errors that occur
        st.error(f"❌ Failed to connect to history sheet: {e}")
        return None
        
# ========== Load User History ==========
def load_user_history(user_email, sheet):
    """Load user's quotation history from Google Sheet"""
    if sheet is None:
        return []
    try:
        df = get_as_dataframe(sheet)
        df.dropna(how='all', inplace=True)
        user_rows = df[df["User Email"].str.lower() == user_email.lower()]
        history = []
        for _, row in user_rows.iterrows():
            try:
                items = json.loads(row["Items JSON"])
                company_details_raw = row.get("Company Details JSON", "{}")
                try:
                    company_details = json.loads(company_details_raw) if pd.notna(company_details_raw) and company_details_raw.strip() != "" else {}
                except:
                    company_details = {}
                stored_hash = str(row.get("Quotation Hash", "")).strip()
                if not stored_hash or stored_hash.lower() in ("nan", ""):
                    fallback_data = f"{row['Company Name']}{row['Timestamp']}{row['Total']}"
                    stored_hash = hashlib.md5(fallback_data.encode()).hexdigest()
                history.append({
                    "user_email": row["User Email"],
                    "timestamp": row["Timestamp"],
                    "company_name": row["Company Name"],
                    "contact_person": row["Contact Person"],
                    "total": float(row["Total"]),
                    "items": items,
                    "pdf_filename": row["PDF Filename"],
                    "hash": stored_hash,
                    "company_details": company_details
                })
            except Exception as e:
                st.warning(f"⚠ Skipping malformed row: {e}")
                continue
        return history
    except Exception as e:
        st.error(f"❌ Failed to load history: {e}")


# ==========
# 🛠️ REPLACE THESE THREE FUNCTIONS EXACTLY AS BELOW
# ==========

def extract_file_id(url):
    """Robustly extract Google Drive file ID from ANY format."""
    if not url or pd.isna(url):
        return None
    s = str(url).strip()
    # Pattern 1: /file/d/FILE_ID/view[?...]
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', s)
    if match:
        return match.group(1)
    # Pattern 2: uc?export=download&id=FILE_ID
    match = re.search(r'id=([a-zA-Z0-9_-]+)', s)
    if match:
        return match.group(1)
    # Pattern 3: open?id=FILE_ID
    match = re.search(r'open\?id=([a-zA-Z0-9_-]+)', s)
    if match:
        return match.group(1)
    return None

def convert_google_drive_url_for_display(url):
    """Convert ANY Google Drive URL → thumbnail (for Streamlit st.image)"""
    if not url:
        return ""
    s = str(url).strip()
    if s.lower() in ("", "nan"):
        return ""
    fid = extract_file_id(s)
    if fid:
        return f"https://drive.google.com/thumbnail?id={fid}&sz=w300"
    # Fallback: return raw string (so you can debug)
    return s

def convert_google_drive_url_for_storage(url):
    """Convert ANY Google Drive URL → direct download (for PDF/image fetch)"""
    if not url:
        return ""
    s = str(url).strip()
    if s.lower() in ("", "nan"):
        return ""
    fid = extract_file_id(s)
    if fid:
        return f"https://drive.google.com/uc?export=download&id={fid}"
    return s

# ========== Google Sheets Connection ==========

@st.cache_resource
def get_gsheet_connection():
    """Cached Google Sheets connection using correct sheet ID and scopes"""
    try:
        # Use full scopes (no extra spaces!)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        # Load service account from Streamlit secrets (NO filename needed)
        sa = gspread.service_account_from_dict(st.secrets["gcp_service_account"], scopes=scopes)
        
        # Open by spreadsheet ID
        spreadsheet = sa.open_by_key("1ewjGt576LjSgeoRLGKoc_LxfnnWlai_ArOv1ZeyXEgk")

        # Try to get the worksheet
        try:
            worksheet = spreadsheet.worksheet("Sheet1")
            st.write("✅ Connected to Google Sheet with Sheet1 worksheet!")
            return worksheet
        except gspread.exceptions.WorksheetNotFound:
            st.error("❌ Worksheet 'Sheet1' not found.")
            
            # List available worksheets for debugging
            worksheets = spreadsheet.worksheets()
            st.write(f"📋 Available worksheets: {[ws.title for ws in worksheets]}")
            return None

    except gspread.exceptions.SpreadsheetNotFound:
        st.error("❌ Spreadsheet not found. Check the ID and sharing.")
        st.info("💡 Make sure you shared the sheet with: amjadquotation@quotationappamjad.iam.gserviceaccount.com")
        return None
        
    except gspread.exceptions.APIError as api_error:
        st.error(f"❌ Google API Error: {api_error}")
        st.info("💡 Check if Google Sheets & Drive APIs are enabled in the GCP project.")
        return None
        
    except Exception as e:
        st.error(f"❌ Unexpected error connecting to Google Sheets: {e}")
        return None

    except gspread.exceptions.SpreadsheetNotFound:
        st.error("❌ Spreadsheet not found. Check the ID and sharing.")
        st.info("💡 Make sure you shared the sheet with: amjadquotation@quotationappamjad.iam.gserviceaccount.com")
        return None
    except gspread.exceptions.APIError as api_error:
        st.error(f"❌ Google API Error: {api_error}")
        st.info("💡 Check if Google Sheets & Drive APIs are enabled.")
        return None
    except Exception as e:
        st.error(f"❌ Unexpected error: {e}")
        return None

        
@st.cache_data(ttl=300)
def get_sheet_data(_sheet):
    """
    Fetch and process sheet data using RAW gspread API
    This ensures NO rows are dropped unexpectedly
    """
    if _sheet is None:
        return None
    
    try:
        # 🔥 USE RAW API - Get ALL values directly from sheet
        # This is more reliable than get_as_dataframe
        all_values = _sheet.get_all_values()
        
        if not all_values or len(all_values) < 2:
            st.error("❌ Sheet is empty or has no data rows.")
            return pd.DataFrame()
        
        # First row is headers
        headers = all_values[0]
        data_rows = all_values[1:]  # All remaining rows
        
        # Create DataFrame manually
        df = pd.DataFrame(data_rows, columns=headers)
        
        # Define expected columns
        expected_cols = [
            "Drawing",           # A
            "Image Featured",    # B 
            "Title",             # C
            "SKU",               # D
            "Size (mm)",         # E
            "Color",             # F
            "Content",           # G
            "Unit Price"         # H
        ]
        
        # Take only first 8 columns and rename them
        if len(df.columns) >= 8:
            df = df.iloc[:, :8].copy()
            df.columns = expected_cols
        else:
            st.error(f"❌ Sheet has fewer than 8 columns. Found: {len(df.columns)}")
            return pd.DataFrame()
        
        # 🔧 Filter out COMPLETELY empty rows (where all cells are empty)

        
        # 🔧 Now filter: keep only rows with valid Title
        df['Title'] = df['Title'].astype(str).str.strip()
        
        # Remove rows where Title is empty or just whitespace
        valid_title_mask = df['Title'].apply(lambda x: x != '' and x.lower() not in ['nan', 'none', 'null'])
        df = df[valid_title_mask].copy()
        
        # Reset index to maintain clean row numbering
        df = df.reset_index(drop=True)
        
        if df.empty:
            st.error("❌ No valid products found after filtering.")
            return pd.DataFrame()
        
        # 🔧 Clean Unit Price column - convert to numeric
        if 'Unit Price' in df.columns:
            def clean_price(val):
                try:
                    # Remove any currency symbols, commas, spaces
                    val_str = str(val).replace('SAR', '').replace(',', '').strip()
                    if val_str == '' or val_str.lower() in ['nan', 'none', 'null']:
                        return 0.0
                    return float(val_str)
                except:
                    return 0.0
            
            df['Unit Price'] = df['Unit Price'].apply(clean_price)
        
        # 🔧 Process Image URLs
        if 'Image Featured' in df.columns:
            def process_image_url(url):
                url_str = str(url).strip()
                if url_str == '' or url_str.lower() in ['nan', 'none', 'null']:
                    return ""
                # Convert Google Drive URLs
                return convert_google_drive_url_for_storage(url_str)
            
            df['Image Featured'] = df['Image Featured'].apply(process_image_url)
        
        # 🔧 Process Drawing URLs  
        if 'Drawing' in df.columns:
            def process_image_url(url):
                url_str = str(url).strip()
                if url_str == '' or url_str.lower() in ['nan', 'none', 'null']:
                    return ""
                return convert_google_drive_url_for_storage(url_str)
            
            df['Drawing'] = df['Drawing'].apply(process_image_url)
        
        # 🔧 Clean SKU column
        if 'SKU' in df.columns:
            def clean_sku(val):
                val_str = str(val).strip()
                if val_str == '' or val_str.lower() in ['nan', 'none', 'null']:
                    return ''
                return val_str
            
            df['SKU'] = df['SKU'].apply(clean_sku)
        
        # 🔧 Clean other text columns
        for col in ['Size (mm)', 'Color', 'Content']:
            if col in df.columns:
                def clean_text(val):
                    val_str = str(val).strip()
                    if val_str == '' or val_str.lower() in ['nan', 'none', 'null']:
                        return ''
                    return val_str
                
                df[col] = df[col].apply(clean_text)
        
        st.success(f"✅ Loaded {len(df)} products from sheet")
        
        return df
        
    except Exception as e:
        st.error(f"❌ Error processing sheet: {e}")
        import traceback
        st.error(traceback.format_exc())
        return None

def load_user_history_from_sheet(user_email, sheet):
    """Load user's quotation history from Google Sheet"""
    if sheet is None:
        return []
    try:
        df = get_as_dataframe(sheet)
        df.dropna(how='all', inplace=True)  
        user_rows = df[df["User Email"].str.lower() == user_email.lower()]
        history = []
        import json
        for _, row in user_rows.iterrows():
            try:
                items = json.loads(row["Items JSON"])
                history.append({
                    "user_email": row["User Email"],
                    "timestamp": row["Timestamp"],
                    "company_name": row["Company Name"],
                    "contact_person": row["Contact Person"],
                    "total": float(row["Total"]),
                    "items": items,
                    "pdf_filename": row["PDF Filename"],
                    "hash": row["Quotation Hash"]
                })
            except Exception as e:
                st.warning(f"⚠ Skipping malformed row: {e}")
                continue
        return history
    except Exception as e:
        st.error(f"❌ Failed to load history: {e}")
        return []

# ========== Image Display Functions ==========
@st.cache_data(show_spinner=False)
def fetch_image_bytes(url):
    # Check if the URL contains a pipe character (multiple URLs)
    if "|" in url:
        # Use only the first URL
        url = url.split("|")[0].strip()
    
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    return resp.content

# ====== Display Product Image ======
def display_product_image(c2, prod, image_url, width=100):
    """Display product image with better error visibility"""
    img_url = convert_google_drive_url_for_display(image_url)
    with c2:
        if img_url and img_url != "":
            try:
                img_bytes = fetch_image_bytes(img_url)
                if img_bytes:
                    img = PILImage.open(BytesIO(img_bytes))
                    st.image(img, caption=prod, use_column_width=True)
                else:
                    st.warning("⚠️ Image unavailable")
                    st.caption(f"URL: {img_url[:50]}...")
            except Exception as e:
                st.error("❌ Image Error")
                st.caption(f"{str(e)[:100]}")
        else:
            st.info("📷 No image URL")
            if image_url and image_url != "":
                st.caption(f"Raw: {str(image_url)[:50]}")


def display_admin_preview(image_url, caption="Image Preview"):
    """Display image preview in admin panel"""
    if image_url:
        try:
            display_url = convert_google_drive_url_for_display(image_url)
            st.image(display_url, caption=caption, width=200)
            st.success("✅ Image loaded successfully!")
        except Exception as e:
            st.error("❌ Could not load image. Please check the URL.")
            st.info("💡 Make sure to use a valid Image Featured or Google Drive link")
    else:
        st.info("📷 Enter an Image Featured above to see preview")

# ========== Login Interface ==========
if not st.session_state.logged_in:
    st.title("Login")
    
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submit_login = st.form_submit_button("Login")
        
        if submit_login:
            user = USERS.get(email)
            if user and user["password"] == password:
                st.session_state.logged_in = True
                st.session_state.user_email = email
                st.session_state.username = user["username"]
                st.session_state.name = user["full_name"]
                st.session_state.role = user["role"]
                st.rerun()
            else:
                st.error("❌ Incorrect email or password.")
    st.stop()

# ========== Logout & History Sidebar ==========
st.sidebar.success(f"Logged in as: {st.session_state.user_email} ({st.session_state.role})")


if st.session_state.logged_in and not st.session_state.history_loaded:
    history_sheet = get_history_sheet()
    if history_sheet:
        st.session_state.history = load_user_history_from_sheet(st.session_state.user_email, history_sheet)
        st.session_state.history_loaded = True
        # Optional: st.info(f"✅ Loaded {len(st.session_state.history)} quotations")
    else:
        st.warning("⚠ Could not connect to Google Sheet. History may be incomplete.")


# 📜 History Button (Visible to all logged-in users)
if st.session_state.role in ["buyer", "admin"]:
    if st.sidebar.button("📜 Quotation History"):
        st.switch_page(Path("pages") / "history.py")

# Logout Button
if st.sidebar.button("Logout"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

# parsing price
def parse_price(price_str):
    if not price_str:
        return 0.0
    match = re.search(r'[\d,.]+', price_str.replace('', ''))
    if match:
        try:
            return float(match.group())
        except:
            return 0.0
    return 0.0

# ========== App Title ==========
st.title("🧾 Price Generator")

# Refresh button
if st.button("🔄 Refresh Sheet Data"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()


@st.cache_data(ttl=300)
def compute_product_lookups(df_hash):
    worksheet = get_gsheet_connection()
    df = get_sheet_data(worksheet)
    if df is None or df.empty:
        st.warning("⚠️ No data loaded from sheet")
        return None
    
    df = df.copy()
    df['original_order'] = range(len(df))
    
    # Create products list with UNIQUE keys (index + title)
    products = []
    price_map = {}
    desc_map = {}
    image_map = {}
    code_map = {}
    size_map = {}
    title_to_key_map = {}  # Map: display_name -> unique_key
    
    for idx, row in df.iterrows():
        title = row['Title']
        sku = str(row.get('SKU', '')).strip()
        
        # Create UNIQUE key: combine index and title
        unique_key = f"{idx}_{title}"
        display_name = title
        
        # If SKU exists, append it to display name for clarity
        if sku and sku not in ['', 'nan', 'NaN', 'None']:
            display_name = f"{title} ({sku})"
        
        products.append(display_name)
        title_to_key_map[display_name] = unique_key
        
        # Store all mappings using unique key
        price_map[display_name] = float(row.get('Unit Price', 0.0))
        
        desc_val = row.get('Content', '')
        desc_map[display_name] = '' if desc_val in ['nan', 'NaN', 'None'] else str(desc_val)

     
        # Inside the loop over df rows:
        size_val = row.get('Size (mm)', '')
        size_map[display_name] = '' if size_val in ['nan', 'NaN', 'None', ''] else str(size_val)
        
        image_val = row.get('Image Featured', '')
        image_map[display_name] = '' if image_val in ['nan', 'NaN', 'None', ''] else str(image_val)
        
        # SKU mapping
        if sku and sku not in ['', 'nan', 'NaN', 'None']:
            code_map[display_name] = sku
        else:
            code_map[display_name] = ''
    
    # Create reverse code map (SKU -> display_name)
    reverse_code_map = {}
    for product, code in code_map.items():
        if code and code not in reverse_code_map:  # Keep first occurrence
            reverse_code_map[code] = product
    
    # Create code_options list (ALL unique SKUs)
    code_options = []
    for product in products:
        code = code_map.get(product, '')
        if code and code not in code_options:
            code_options.append(code)
    
    return {
        'products': products,              # All 85 products with unique display names
        'price_map': price_map,
        'desc_map': desc_map,
        'image_map': image_map,
        'code_map': code_map,
        'reverse_code_map': reverse_code_map,
        'code_options': code_options,      
        'size_map': size_map,
        'title_to_key_map': title_to_key_map
    }
    
# 🚀 Load product
lookups = compute_product_lookups("v1")
if lookups is None:
    st.error("❌ No product data loaded")
    st.stop()



# ========== Admin Panel ==========
if st.session_state.role == "admin":
    st.header("🔧 Admin Panel")
    if 'admin_choice' not in st.session_state:
        st.session_state.admin_choice = None
    if st.session_state.admin_choice is None:
        st.subheader("Choose Your Action:")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🗃 Edit Database", use_container_width=True, help="Add, update, or delete products"):
                st.session_state.admin_choice = "database"
                st.rerun()
        with col2:
            if st.button("📋 Make Quotation", use_container_width=True, help="Create quotation for customers"):
                st.session_state.admin_choice = "quotation"
                st.rerun()
        st.info("👆 Please select what you would like to do")
        st.stop()
    
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("← Back to Menu"):
            st.session_state.admin_choice = None
            st.rerun()
    with col2:
        if st.session_state.admin_choice == "database":
            st.markdown("Current Mode: 🗃 Database Management")
        else:
            st.markdown("Current Mode: 📋 Quotation Creation")
    
    
    
    st.markdown("---")
    if st.session_state.admin_choice == "database":  
        tab1, tab2, tab3 = st.tabs(["➕ Add Product", "🗑 Delete Product", "✏ Update Product"])
        with tab1:
            st.subheader("Add New Product")
            form_col, image_col = st.columns([2, 1])
            
            with form_col:
                with st.form("add_product_form"):
                    new_name = st.text_input("Product Name*", help="Required field")
                    new_price = st.number_input("Price per Item", min_value=0.0, format="%.2f")
                    new_sku = st.text_input("SKU (Product Code)", help="Optional: Unique product identifier like CHAIR-001")
                    new_desc = st.text_area("Description / Material")
                    new_image = st.text_input("Image URL (Optional)", help="Direct image URL from WordPress")
                    
                    if st.form_submit_button("✅ Create in WordPress"):
                        if not new_name:
                            st.error("❌ Product name is required")
                        else:
                            product_data = {
                                "name": new_name,
                                "type": "simple",
                                "SKU": new_sku,
                                "regular_price": str(new_price),
                                "description": new_desc,
                                "status": "publish"
                            }
                            
                            # Add image if provided
                            if new_image.strip():
                                product_data["images"] = [{"src": new_image.strip()}]

                            
                            # Send to WordPress
                            if create_product_in_woocommerce(product_data):
                                st.cache_data.clear()  # Force refresh
                                st.rerun()
        with tab2:
            st.subheader("Delete Product")
            
            # Use product names from lookups
            product_names = lookups['products']
            product_to_delete = st.selectbox("Select product to delete", product_names)
            
            confirm = st.checkbox(f"I want to delete '{product_to_delete}'")
            
            if st.button("🗑 Permanently Delete") and confirm:
                if product_to_delete in lookups['products']:
                    # Find the product ID
                    # Get full WordPress products to find ID
                    wordpress_products = get_wordpress_products()
                    if wordpress_products is None:
                        st.error("❌ Could not load products from WordPress.")
                        st.stop()

                    # Find the product ID
                    product_id = None
                    for p in wordpress_products:
                        if p["name"] == product_to_delete:
                            product_id = p["id"]
                            break

                    if not product_id:
                        st.error(f"❌ Could not find product ID for '{product_to_delete}' in WordPress.")
                        st.stop()
                    
                    if not product_id:
                        st.error("❌ Could not find product ID")
                    else:
                        # Call delete function
                        if delete_product_in_woocommerce(product_id):
                            st.cache_data.clear()
                            st.rerun()
                else:
                    st.error(f"❌ Product '{product_to_delete}' not found.")
            elif not confirm:
                st.warning("❌ Please confirm deletion")
            
            

        with tab3:
            st.subheader("Update Product")
            form_col, image_col = st.columns([2, 1])
            
            with form_col:
                # Use product names from lookups
                product_names = lookups['products']
                selected_product = st.selectbox(
                    "Select product to update", 
                    product_names, 
                    key="update_product_select"
                )
                
                # Find the product details
                if selected_product != "-- Select --" and selected_product in lookups['products']:
                    with st.form("update_product_form"):
                        updated_name = st.text_input("Product Name", value=selected_product)
                        updated_price = st.number_input(
                            "Price", 
                            value=lookups['price_map'].get(selected_product, 0.0),
                            min_value=0.0,
                            format="%.2f"
                        )
                        updated_sku = st.text_input("SKU (Product Code)", 
                            value=lookups['code_map'].get(selected_product, ""),
                            help="Edit product code"
                        )

                        updated_desc = st.text_area(
                            "Description", 
                            value=lookups['desc_map'].get(selected_product, "")
                        )
                        updated_image = st.text_input(
                            "Image URL", 
                            value=lookups['image_map'].get(selected_product, "")
                        )
                        
                        if st.form_submit_button("✅ Update in WordPress"):
                            # Find the product ID from the raw WordPress data
                            # Get full WordPress products
                            wordpress_products = get_wordpress_products()
                            if wordpress_products is None:
                                st.error("❌ Could not load products from WordPress.")
                                st.stop()

                            # Find the product ID
                            product_id = None
                            for p in wordpress_products:
                                if p["name"] == selected_product:
                                    product_id = p["id"]
                                    break

                            if not product_id:
                                st.error("❌ Could not find product ID in WordPress")
                                st.stop()
                            
                            if not product_id:
                                st.error("❌ Could not find product ID in WordPress")
                            else:
                                # Build update data
                                update_data = {
                                    "name": updated_name,
                                    "regular_price": str(updated_price),
                                    "description": updated_desc,
                                }
                                
                                if updated_image.strip():
                                    update_data["images"] = [{"src": updated_image.strip()}]
                                    
                                # Send update to WordPress
                                if update_product_in_woocommerce(product_id, update_data):
                                    st.cache_data.clear()  # Force refresh
                                    st.rerun()
                else:
                    st.info("Please select a product to update")

            st.stop()

# ========== Buyer Panel ==========

st.header("🛍 Buy Products & Get Quotation")

if st.session_state.get('form_submitted', False):
    st.subheader("Choose an option:")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✏️ Edit Company Info", use_container_width=True):
            st.session_state.form_submitted = False
            # Preserve the current company name in session state
            current_company_name = st.session_state.company_details.get("company_name", "")
            if current_company_name:
                st.session_state.editing_company = current_company_name
            else:
                st.session_state.editing_company = None
            st.rerun()
    with col2:
        if st.button("🆕 Create New Quotation", use_container_width=True):
            st.session_state.edit_mode = False
            st.session_state.form_submitted = False
            # Preserve default values from last quotation
            old_details = st.session_state.company_details
            st.session_state.company_details = {
                "company_name": "",
                "contact_person": "",
                "contact_email": "",
                "contact_phone": "",
                "address": "",
                # Keep defaults from last quote
                "prepared_by": st.session_state.name,
                "prepared_by_email": st.session_state.user_email,
                "current_date": datetime.now().strftime("%A, %B %d, %Y"),
                "valid_till": (datetime.now() + timedelta(days=10)).strftime("%A, %B %d, %Y"),
                "quotation_validity": "30 days"
            }
            # Clear cart and items
            st.session_state.cart = []
            st.session_state.pdf_data = []
            st.session_state.selected_products = {}
            st.session_state.row_indices = [0]
            st.success("🆕 New quotation started - all items cleared!")
            st.rerun()

# Company details form
if not st.session_state.form_submitted:
    st.subheader("🏢 Company and Contact Details")

    # Load company sheet and data
    company_sheet = get_company_sheet()
    existing_companies = load_company_data(company_sheet)
    company_names = [c["company_name"] for c in existing_companies if c.get("company_name")]

   
    # Restore previously selected company if editing
    if "editing_company" in st.session_state and st.session_state.editing_company:
        try:
            # Find index in company_names, then +1 for the "-- Create New --" offset
            default_index = company_names.index(st.session_state.editing_company) + 1
        except ValueError:
            # If company not found in list, default to 0
            default_index = 0
    else:
        default_index = 0

    selected_company = st.selectbox(
        "Or select existing company",
        ["-- Create New --"] + company_names,
        index=default_index,
        key="select_company"
    )
    # After selection, store it in session_state if not creating new
    if selected_company != "-- Create New --":
        st.session_state.editing_company = selected_company
    else:
        st.session_state.editing_company = None

    # Pre-fill form if a company is selected
    if selected_company != "-- Create New --":
        selected_data = next(c for c in existing_companies if c["company_name"] == selected_company)
    else:
        selected_data = {}

    # Form inputs
    with st.form(key="company_details_form"):
        company_name = st.text_input(
            "Company Name",
            value=selected_data.get("company_name", ""),
            placeholder="Enter Company Name (mandatory)"
        )
        contact_person = st.text_input(
            "Contact Person",
            value=selected_data.get("contact_person", ""),
            placeholder="Enter contact person (mandatory)"
        )
        contact_email = st.text_input(
            "Contact Email",
            value=selected_data.get("contact_email", ""),
            placeholder="Enter contact email (optional)"
        )
        contact_phone = st.text_input(
            "Contact Cell Phone",
            value=selected_data.get("contact_phone", ""),
            placeholder="Enter contact cell phone (optional)"
        )
        address = st.text_area(
            "Address (Optional)",
            value=selected_data.get("address", ""),
            placeholder="Enter address (optional)"
        )

        # Prepared by (auto-filled)
        prepared_by = st.session_state.name
        prepared_by_email = st.session_state.user_email

        # Date fields
        current_date = datetime.now().strftime("%A, %B %d, %Y")
        valid_till = (datetime.now() + timedelta(days=10)).strftime("%A, %B %d, %Y")
        quotation_validity = "30 days"

        # Validation patterns
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        phone_pattern = r'^\+?\d+$'

        submit = st.form_submit_button("Submit Details")
        if submit:
            # Required fields (only Company and Contact Person)
            if not company_name:
                st.warning("⚠ Please enter the Company Name.")
            elif not contact_person:
                st.warning("⚠ Please enter the Contact Person.")
            else:
                # Validate phone only if provided
                if contact_phone.strip():
                    if not re.match(phone_pattern, contact_phone.strip()):
                        st.error("❌ Invalid phone number format.")
                    else:
                        # Clean phone number for storage
                        try:
                            num = float(contact_phone.strip())
                            contact_phone = str(int(num)) if num.is_integer() else str(num)
                        except (ValueError, TypeError):
                            contact_phone = contact_phone.strip()

                # Validate email only if provided
                if contact_email.strip():
                    if not re.match(email_pattern, contact_email.strip()):
                        st.error("❌ Invalid email format.")
                        st.stop()  # Stop if email is invalid

                # All validations passed
                st.session_state.form_submitted = True
                st.session_state.company_details = {
                    "company_name": company_name.strip(),
                    "contact_person": contact_person.strip(),
                    "contact_email": contact_email.strip(),
                    "contact_phone": contact_phone.strip(),
                    "address": address.strip(),
                    "prepared_by": prepared_by,
                    "prepared_by_email": prepared_by_email,
                    "current_date": current_date,
                    "valid_till": valid_till,
                    "quotation_validity": quotation_validity
                }

                # Save to Google Sheet only if it's a new company or edited
                if selected_company == "-- Create New --" or selected_company != company_name:
                    save_company_to_sheet(company_sheet, st.session_state.company_details)
                else:
                    st.info(f"ℹ '{company_name}' data updated in session.")
                
                st.rerun()
    
    st.stop()  
# ========== Product Selection Interface ==========
company_details = st.session_state.company_details

price_map = lookups['price_map']
desc_map = lookups['desc_map']
size_map = lookups['size_map']
image_map = lookups['image_map']
code_map = lookups['code_map']
reverse_code_map = lookups['reverse_code_map']
name_options = ["-- Select --"] + lookups['products']
code_options = ["-- Select --"] + lookups['code_options']

st.markdown(f" Quotation for {company_details['company_name']}")

# Product selection headers
cols = st.columns([2, 1.5, 2, 1.5, 2, 2, 2, 2, 0.5])
headers = ["Product", "Code", "Image", "Color", "Price", "Quantity", "Discount %", "Total", "Clr"]
for i, header in enumerate(headers):
    cols[i].markdown(f"{header}")

output_data = []
total_sum = 0
checkDiscount = False
basePrice = 0.0

# Render product rows
for idx in st.session_state.row_indices:
    c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns([2, 1.5, 2, 1.5, 2, 2, 2, 2, 0.5])

    prod_key = f"prod_{idx}"
    name_key = f"name_{prod_key}"
    code_key = f"code_{prod_key}"
    sync_flag_key = f"syncing_{prod_key}"

    # Initialize session defaults
    if prod_key not in st.session_state.selected_products:
        st.session_state.selected_products[prod_key] = "-- Select --"
    if name_key not in st.session_state:
        st.session_state[name_key] = "-- Select --"
    if code_key not in st.session_state:
        st.session_state[code_key] = "-- Select --"


    # Define two-way sync callbacks
    def on_name_change(_name_key=name_key, _code_key=code_key, _prod_key=prod_key, _flag_key=sync_flag_key):
        if st.session_state.get(_flag_key):
            return
        st.session_state[_flag_key] = True
        sel_name = st.session_state.get(_name_key, "-- Select --")
        if sel_name != "-- Select --":
            code_val = code_map.get(sel_name, "")
            if pd.notna(code_val) and str(code_val).strip() not in ["", "nan"]:
                st.session_state[_code_key] = str(code_val).strip()
            else:
                st.session_state[_code_key] = "-- Select --"
            st.session_state.selected_products[_prod_key] = sel_name
        else:
            st.session_state[_code_key] = "-- Select --"
            st.session_state.selected_products[_prod_key] = "-- Select --"
        st.session_state[_flag_key] = False

    def on_code_change(_name_key=name_key, _code_key=code_key, _prod_key=prod_key, _flag_key=sync_flag_key):
        if st.session_state.get(_flag_key):
            return
        st.session_state[_flag_key] = True
        sel_code = st.session_state.get(_code_key, "-- Select --")
        if sel_code != "-- Select --" and sel_code in reverse_code_map:
            resolved_name = reverse_code_map[sel_code]
            st.session_state[_name_key] = resolved_name
            st.session_state.selected_products[_prod_key] = resolved_name
        else:
            st.session_state[_name_key] = "-- Select --"
            st.session_state.selected_products[_prod_key] = "-- Select --"
        st.session_state[_flag_key] = False

    # Render both selectboxes, using current session values
    try:
        name_index = name_options.index(st.session_state[name_key]) if st.session_state[name_key] in name_options else 0
    except:
        name_index = 0
    try:
        code_index = code_options.index(st.session_state[code_key]) if st.session_state[code_key] in code_options else 0
    except:
        code_index = 0

    col_name, col_code = c1, c2
    col_name.selectbox(
        "Product Name",
        name_options,
        key=name_key,
        index=name_index,
        label_visibility="collapsed",
        on_change=on_name_change
    )
    col_code.selectbox(
        "SKU Code",
        code_options,
        key=code_key,
        index=code_index,
        label_visibility="collapsed",
        on_change=on_code_change
    )

    # Resolved selected product for this row
    prod = st.session_state.selected_products.get(prod_key, "-- Select --")

    # Clear button clears both name/code and the product
    if c9.button("X", key=f"clear_{idx}"):
        st.session_state.row_indices.remove(idx)
        st.session_state.selected_products.pop(prod_key, None)
        for k in (name_key, code_key, sync_flag_key):
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    # If a product is selected, render details and compute totals
    if prod != "-- Select --":
        unit_price = price_map.get(prod, 0.0)
        qty = c6.number_input("", min_value=1, value=1, step=1, key=f"qty_{idx}", label_visibility="collapsed")
        discount = c7.number_input("", min_value=0.0, max_value=100.0, value=0.0, step=1.0, key=f"disc_{idx}", label_visibility="collapsed")

        color_key = f"color_{idx}"

        # Initialize only if not exists AND no widget has claimed it yet
        if color_key not in st.session_state:
            st.session_state[color_key] = "Choose color"

        if prod != "-- Select --":
            user_color = c4.text_input(  
                "Color",
                value=st.session_state[color_key],
                key=color_key,
                label_visibility="collapsed"
            )
        else:
            c4.write("—")
            user_color = "Choose color"

        valid_discount = 0.0 if discount > 20 else discount
        if discount > 20:
            st.warning(f"⚠ Max 20% discount allowed for '{prod}'. Ignoring discount.")
        if valid_discount > 0:
            checkDiscount = True

        basePrice += unit_price * qty
        discounted_price = unit_price * (1 - valid_discount / 100)
        line_total = discounted_price * qty

        # Display image directly without download
        image_url = image_map.get(prod, "")
        display_product_image(c3, prod, image_url)

        c5.write(f"{unit_price:.2f} SAR")
        c8.write(f"{line_total:.2f} SAR")

        original_image_urls = lookups['image_map'].get(prod, "")

       
        output_data.append({
            "Item": prod,
            "Description": desc_map.get(prod, ""),          
            "Size (mm)": size_map.get(prod, ""), 
            "Color": user_color,                            
            "Image": original_image_urls,
            "Quantity": qty,
            "Price per item": unit_price,
            "Discount %": valid_discount,
            "Total price": line_total
        })
        total_sum += line_total
    else:
        for col in [c2, c3, c4, c5, c6]:
            col.write("—")

# Add product button
if st.button("➕ Add Product"):
    st.session_state.row_indices.append(max(st.session_state.row_indices, default=-1) + 1)
    st.rerun()

# Calculate totals
st.markdown("---")
final_total = total_sum

# if not checkDiscount:
overall_discount = st.number_input("🧮 Overall Quotation Discount (%)", min_value=0.0, max_value=100.0, step=1.0, value=0.0)
if overall_discount > 20:
    st.warning("⚠ Overall discount cannot exceed 20%. Ignoring discount.")
    overall_discount = 0.0
basePrice = total_sum
final_total = total_sum * (1 - overall_discount / 100)
st.markdown(f"💰 *Total Before Discount: {total_sum:.2f} SAR")
st.markdown(f"🔻 *Discount Applied: {overall_discount:.0f}%")
st.markdown(f"🧾 *Final Total: {final_total:.2f} SAR")
# else:
#     st.markdown("⚠ You cannot add overall discount when individual discounts are applied")

st.markdown("---")
st.markdown(f"### 💰 Grand Total: {final_total:.2f} SAR")

if output_data:
    st.dataframe(pd.DataFrame(output_data), use_container_width=True)

# ========== PDF Generation Functions ==========
def download_image_for_pdf(url, max_size=(300, 300)):
    """Download and resize image for PDF with better error handling"""
    try:
        if not url or url == "":
            return None
        
        # Handle multiple URLs
        if "|" in url:
            url = url.split("|")[0].strip()
        
        # Convert Google Drive URL if needed
        download_url = convert_google_drive_url_for_storage(url)
        
        # Try to download
        response = requests.get(download_url, timeout=10)
        response.raise_for_status()
        
        # Open and process image
        img = PILImage.open(BytesIO(response.content))
        
        # Convert to RGB if needed (handles PNG with alpha)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = PILImage.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        
        # Resize while maintaining aspect ratio
        img_ratio = img.width / img.height
        max_width, max_height = max_size
        
        if img.width > max_width or img.height > max_height:
            if img_ratio > 1:
                # Wider than tall
                new_width = max_width
                new_height = int(max_width / img_ratio)
            else:
                # Taller than wide
                new_height = max_height
                new_width = int(max_height * img_ratio)
            img = img.resize((new_width, new_height), PILImage.Resampling.LANCZOS)
        
        # Save as PNG
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        img.save(temp_file, format="PNG")
        temp_file.close()
        return temp_file.name
        
    except requests.exceptions.Timeout:
        print(f"Timeout downloading image: {url[:50]}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error downloading image ({e.response.status_code}): {url[:50]}")
        return None
    except Exception as e:
        print(f"Error processing image from {url[:50]}: {e}")
        return None

@st.cache_data
def build_pdf_cached(data_hash, final_total, company_details,
                    hdr_path="amjad_quotation_header.png",
                    ftr_path="amjad_quotation_footer.png"):
    """
    Generate a professional quotation PDF with:
    - Terms & Conditions (image + text) moved BEFORE the items table
    - Page break after terms to force table to start on second page
    - All other styling and layout preserved
    """
    
    USE_TWO_IMAGES = False 

    def build_pdf(data, total, company_details, hdr_path, ftr_path):
        # Ensure data exists
        if not data:
            st.error("❌ No product data to generate PDF.")
            return None

        # Create temporary PDF file
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        pdf_path = tmp.name
        tmp.close()

        # Setup document with A3 size
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A3,
            topMargin=250,
            leftMargin=45,
            rightMargin=45,
            bottomMargin=150
        )
        styles = getSampleStyleSheet()
        elems = []

        # Base text style
        styles['Normal'].fontSize = 14
        styles['Normal'].leading = 20

        # Left-aligned paragraph style
        aligned_style = ParagraphStyle(
            name='LeftAligned',
            parent=styles['Normal'],
            leftIndent=0,
            firstLineIndent=0,
            alignment=0,  # Left-aligned
            spaceBefore=12,
            spaceAfter=12
        )

        # ======================
        # Header & Footer Functions
        # ======================
        # ======================
        # First Page: Header + Footer
        # ======================
        def first_page(canvas, doc):
            canvas.saveState()

            # === Header (only on first page) ===
            if hdr_path and os.path.exists(hdr_path):
                img = PILImage.open(hdr_path)
                w, h = img.size
                page_w = doc.width + doc.leftMargin + doc.rightMargin
                header_h = page_w * (h / w)
                canvas.drawImage(
                    hdr_path,
                    x=0,
                    y=doc.pagesize[1] - header_h,
                    width=page_w,
                    height=header_h,
                    preserveAspectRatio=True,
                    mask='auto'
                )

            # === Footer (on all pages) ===
            footer_y = 0
            if ftr_path and os.path.exists(ftr_path):
                img = PILImage.open(ftr_path)
                w, h = img.size
                page_w = doc.width + doc.leftMargin + doc.rightMargin
                footer_h = page_w * (h / w)
                canvas.drawImage(
                    ftr_path,
                    x=0,
                    y=0,
                    width=page_w,
                    height=footer_h,
                    preserveAspectRatio=True,
                    mask='auto'
                )
                footer_y = footer_h

            # === Page Number ===
            canvas.setFont('Helvetica', 10)
            canvas.drawRightString(
                doc.width + doc.leftMargin,
                footer_y + 15,
                str(canvas.getPageNumber())
            )

            canvas.restoreState()

        # ======================
        # Later Pages: Footer Only
        # ======================
        def later_pages(canvas, doc):
            canvas.saveState()

            # === Footer only (no header) ===
            footer_y = 0
            if ftr_path and os.path.exists(ftr_path):
                img = PILImage.open(ftr_path)
                w, h = img.size
                page_w = doc.width + doc.leftMargin + doc.rightMargin
                footer_h = page_w * (h / w)
                canvas.drawImage(
                    ftr_path,
                    x=0,
                    y=0,
                    width=page_w,
                    height=footer_h,
                    preserveAspectRatio=True,
                    mask='auto'
                )
                footer_y = footer_h

            # === Page Number ===
            canvas.setFont('Helvetica', 10)
            canvas.drawRightString(
                doc.width + doc.leftMargin,
                footer_y + 15,
                str(canvas.getPageNumber())
            )

            canvas.restoreState()

        # ======================
        # Company Details
        # ======================
        # Build company details dynamically
        company_lines = [
         
        ]

        # Always required
        company_lines.append(f"<b><font color=\"maroon\">Date:</font></b> {company_details.get('current_date', '')}")
        company_lines.append(f"<b><font color=\"maroon\">Valid Till:</font></b> {company_details.get('valid_till', '')}")
        company_lines.append(f"<b><font color=\"maroon\">Quotation Validity:</font></b> {company_details.get('validation_days', '')} days")
        company_lines.append(f"<b><font color=\"maroon\">Contact Person:</font></b> {company_details.get('prepared_by', '')}")
        company_lines.append(f"<b><font color=\"maroon\">Email:</font></b> {company_details.get('prepared_by_email', '')}")
        company_lines.append(f"<b><font color=\"maroon\">Phone Number:</font></b> +966 55 063 2094")

        # Only show Address if not empty

        # Add spacing
        company_lines.append("")
        company_lines.append("")

        # Prepared by section

        company_lines.append(f"<b><font color=\"black\">Company:</font></b> {company_details.get('company_name', '')}")
        company_lines.append(f"<b><font color=\"black\">Contact Person:</font></b> {company_details.get('contact_person', '')}")
        company_lines.append(f"<b><font color=\"black\">Email:</font></b> {company_details.get('contact_email', '')}")
        company_lines.append(f"<b><font color=\"black\">Phone:</font></b> {company_details.get('contact_phone', '')}")
        address = company_details.get('address', '').strip()
        if address:
            company_lines.append(f"<b><font color=\"Black\">Address:</font></b> {address}")
        company_lines.append("")
        company_lines.append("")
        company_lines.append("")
        # Join all lines
        details = "<para align=\"left\"><font size=14>" + "<br/>".join(company_lines) + "</font></para>"

        elems.append(Paragraph(details, aligned_style))

        # ======================
        # Force items table to start on a new page
        # ======================
        # elems.append(PageBreak())

        # ======================ِ
        # Items Table
        # ======================
        product_table_data = [["Ser.", "Image", "Product", "Color", "Description", "QTY", "Price", "Total"]]
        temp_files = []

        # Original total: 30 + 170 + 90 + 80 + 220 + 30 + 60 + 60 = 730
        # Remove "Color" (80) → redistribute: Image +50 → 220    , Description +30 → 250
        col_widths = [30, 220, 80 ,60, 200, 40, 50, 50]  
        #col_widths = [30, 320, 90, 150, 30, 60, 60]  # Sum = 730 pt / Use this when you need two pics 
        total_table_width = sum(col_widths)

        for idx, r in enumerate(data, start=1):
            # Get all image URLs (comma-separated in session state)
            image_urls = r.get("Image", "")
            image_paths = []

            # Process multiple images (if available)
            if image_urls:
                # Split by pipe character (used as separator in session state)
                urls = [url.strip() for url in image_urls.split("|") if url.strip()]
                
                max_images = 2 if USE_TWO_IMAGES else 1
                for url in urls[:max_images]:  # Use 1 or 2 based on toggle 
                    try:
                        download_url = convert_google_drive_url_for_storage(url)
                        temp_img_path = download_image_for_pdf(download_url, max_size=(300, 300))
                        if temp_img_path:
                            image_paths.append(temp_img_path)
                            temp_files.append(temp_img_path)
                    except Exception as e:
                        print(f"Error loading image: {e}")

            # Create image element
            if image_paths:
                # Define image dimensions
                total_img_width = 210  # Leave 5pt padding on each side / edit this if you wanted another image 
                num_images = min(2 if USE_TWO_IMAGES else 1, len(image_paths))
                img_width = (total_img_width - 10) / num_images  # 10pt gap between images
                img_height = 135  # Good height for A3 row

                # Create image objects
                img_flowables = []
                for path in image_paths[:2]:
                    img = RLImage(path)
                    img.drawWidth = img_width
                    img.drawHeight = img_height
                    img.hAlign = 'CENTER'
                    img.vAlign = 'MIDDLE'
                    img_flowables.append(img)

                # If only one image, center it
                if len(img_flowables) == 1:
                    img_table = Table(
                        [[img_flowables[0]]],
                        colWidths=[total_img_width],
                        hAlign='CENTER'
                    )
                else:  # Two images
                    img_table = Table(
                        [img_flowables],  # Single row with two images
                        colWidths=[img_width, img_width],
                        hAlign='CENTER',
                        spaceAfter=0
                    )

                # Style the table (no borders, centered)
                img_table.setStyle(TableStyle([
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ]))

                # Wrap in KeepInFrame to prevent overflow
                img_element = KeepInFrame(250, 190, [img_table], mode='shrink')
            else:
                img_element = Paragraph("No Image", ParagraphStyle('NoImage', 
                    alignment=1, 
                    fontSize=10, 
                    textColor=colors.grey))

            # Build rich description with bold labels
            # Extract raw fields
            desc = r.get('Description', '').strip()
            size = r.get('Size (mm)', '').strip()
            user_color = r.get('Color', 'Choose from In-Stock Colors').strip()

            # Build combined description: Description + Size
            desc_parts = []
            if desc:
                desc_parts.append(desc)
            if size:
                desc_parts.append(f"Size: {size}")
            full_desc = "<br/>".join(desc_parts) if desc_parts else "—"

            # Create Paragraphs
            desc_style = ParagraphStyle(
                'Desc',
                fontSize=11,
                leading=15,
                alignment=1,  # center
                wordWrap='CJK'
            )
            desc_para = Paragraph(full_desc, desc_style)

            styleN = ParagraphStyle('Center', fontSize=10, leading=17, alignment=1)
            color_para = Paragraph(user_color, styleN)
            # Adjust the description style for better wrapping
            desc_style = ParagraphStyle(
                'Desc', 
                fontSize=11, 
                leading=15,  # Slightly reduced line spacing
                alignment=1,  # center alignment
                wordWrap='CJK'  # Better word wrapping
            )
            desc_para = Paragraph(full_desc, desc_style)

            styleN = ParagraphStyle('Center', fontSize=10, leading=17, alignment=1)

            product_table_data.append([
                str(idx),
                img_element,
                Paragraph(str(r.get('Item', '')), styleN),
                color_para,     
                desc_para,     
                str(r['Quantity']),
                f"{r['Price per item']:.2f}",
                f"{r['Total price']:.2f}"
            ])

        # Create table
        product_table = Table(
            product_table_data,
            colWidths=col_widths,
            rowHeights=[25] + [190] * (len(product_table_data) - 1)
        )
        product_table.setStyle(TableStyle([
            # Header row
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('BACKGROUND', (0, 0), (-1, 0), colors.maroon),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),           # Center header text
            ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),          # Vertically center header
            ('TOPPADDING', (0, 0), (-1, 0), 9),            # Add padding for visual centering
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),

            # Body rows
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (0, 1), (-1, -1), 'CENTER'),          # Center all cell content
            ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),         # Vertical center
            ('TOPPADDING', (0, 1), (-1, -1), 8),            # Better vertical spacing
            ('BOTTOMPADDING', (0, 1), (-1, -1), 8),

            # Grid
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        elems.append(product_table)

        # ======================
        # Summary Table (same width)
        # ======================
        subtotal = sum(float(item['Price per item']) * float(item['Quantity']) for item in data)
        discount_amount = subtotal - total
        vat = total * 0.14
        grand_total = total + vat

        summary_data = [
            ["Subtotal", f"{subtotal:.2f}"],
        ]
        if discount_amount > 0:
            summary_data.append(["Discount", f"{discount_amount:.2f}"])
        summary_data.append(["Net Total", f"{total:.2f}"])
        summary_data.append(["VAT (14%)", f"{vat:.2f}"])
        summary_data.append(["Grand Total", f"{grand_total:.2f}"])

        summary_col_widths = [total_table_width - 120, 120]
        summary_table = Table(summary_data, colWidths=summary_col_widths)
        summary_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -2), 'Helvetica'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),         # Vertical center
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1.0, colors.black),
            ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
            ('TEXTCOLOR', (1, 1), (1, 1), colors.red) if discount_amount > 0 else ('TEXTCOLOR', (1, 1), (1, 1), colors.black),
        ]))
        elems.append(summary_table)

        elems.append(PageBreak())


        # ======================
        # Terms & Conditions (with terms.png)
        # ======================
        terms_img_path = "terms.png"
        if os.path.exists(terms_img_path):
            try:
                img = RLImage(terms_img_path)
                img._restrictSize(doc.width, 80)  # As in original
                img.hAlign = 'CENTER'
                elems.append(Spacer(1, 45))
                elems.append(img)
            except Exception as e:
                print(f"Error adding terms.png: {e}")

        # Now show the actual terms
        terms_text = st.session_state.terms_and_conditions.get("value", "")
        if terms_text:
            import html
            escaped_terms = html.escape(terms_text)
            terms_html = "<br/>".join([f"{line.strip()}" for line in escaped_terms.split('\n') if line.strip()])
            terms_para = Paragraph(f"<font size=12>{terms_html}</font>", aligned_style)
            elems.append(Spacer(1, 20))
            elems.append(terms_para)


        # ======================
        # Build PDF
        # ======================
        try:
            doc.build(elems, onFirstPage=first_page, onLaterPages=later_pages)
        except Exception as e:
            print(f"Error building PDF: {e}")
            raise
        finally:
            # Clean up temp image files
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except:
                    pass

        return pdf_path

    # Ensure pdf_data is always in session state
    st.session_state.pdf_data = st.session_state.get('pdf_data', [])

    # Pass actual data (not empty list) to build_pdf
    return build_pdf(
        st.session_state.pdf_data,
        final_total,
        company_details,
        hdr_path,
        ftr_path
    )

def load_user_history_from_sheet(user_email, sheet):
    """Load user's quotation history from Google Sheet with fallbacks"""
    if sheet is None:
        return []
    try:
        df = get_as_dataframe(sheet)
        df.dropna(how='all', inplace=True)  # Remove completely empty rows
        user_rows = df[df["User Email"].str.lower() == user_email.lower()]
        history = []
        for _, row in user_rows.iterrows():
            try:
                items = json.loads(row["Items JSON"])
                company_details_raw = row.get("Company Details JSON", "{}")
                try:
                    company_details = json.loads(company_details_raw) if pd.notna(company_details_raw) and company_details_raw.strip() != "" else {}
                except:
                    company_details = {}
                # 🔐 Generate fallback hash if not present
                stored_hash = str(row.get("Quotation Hash", "")).strip()
                if not stored_hash or stored_hash.lower() == "nan":
                    # Create deterministic fallback hash
                    fallback_data = f"{row['Company Name']}{row['Timestamp']}{row['Total']}"
                    stored_hash = hashlib.md5(fallback_data.encode()).hexdigest()
                history.append({
                    "user_email": row["User Email"],
                    "timestamp": row["Timestamp"],
                    "company_name": row["Company Name"],
                    "contact_person": row["Contact Person"],
                    "total": float(row["Total"]),
                    "items": items,
                    "pdf_filename": row["PDF Filename"],
                    "hash": stored_hash,  # Always ensure this exists
                    "company_details": company_details
                })
            except Exception as e:
                st.warning(f"⚠ Skipping malformed row (Company: {row.get('Company Name', 'Unknown')}): {e}")
                continue
        return history
    except Exception as e:
        st.error(f"❌ Failed to load history: {e}")
        return []




# Shared Modal: Editable for Admin, Read-only for Buyer
if st.session_state.get("show_edit_terms", False):
    st.subheader("📄 Terms & Conditions")

    if st.session_state.role == "admin":
        # Admin: Editable
        with st.form("edit_terms_form"):
            new_terms = st.text_area(
                "Modify Terms and Conditions",
                value=st.session_state.terms_and_conditions["value"],
                height=400
            )
            terms_reviewed = st.checkbox(
                "I have reviewed the Terms & Conditions",
                help="Check this box after you read the Terms & Conditions",
                value=st.session_state.get("terms_reviewed", False)
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.form_submit_button("✅ Save Terms"):
                    st.session_state.terms_and_conditions["value"] = new_terms.strip()
                    st.session_state.terms_reviewed = terms_reviewed
                    st.session_state.show_edit_terms = False
                    st.success("✅ Terms updated!")
                    st.rerun()
            with col2:
                if st.form_submit_button("❌ Cancel"):
                    st.session_state.terms_reviewed = terms_reviewed
                    st.session_state.show_edit_terms = False
                    st.rerun()
    else:
        # Buyer: Read-only
        st.markdown(
            st.session_state.terms_and_conditions["value"].replace("\n", "<br>"),
            unsafe_allow_html=True
        )
        terms_reviewed = st.checkbox(
            "I have reviewed the Terms & Conditions",
            value=st.session_state.get("terms_reviewed", False)
        )
        if st.button("❌ Close"):
            st.session_state.terms_reviewed = terms_reviewed
            st.session_state.show_edit_terms = False
            st.rerun()
st.markdown("---")
# ======================
# 📅 Validation Period Input
# ======================
st.markdown("### ⏳ Quotation Validity")
col_a, col_b = st.columns([1, 2])

with col_a:
    # Get current validation days from session or default to 30
    current_days = st.session_state.company_details.get("validation_days", 30)
    validation_days = st.number_input(
        "Validation Period (days)",
        min_value=1,
        max_value=365,
        value=int(current_days),
        help="Number of days the quotation remains valid from today"
    )

# Calculate valid_till date
valid_till_date = (datetime.now() + timedelta(days=validation_days)).strftime("%A, %B %d, %Y")

# Update company_details in session state
st.session_state.company_details["validation_days"] = validation_days
st.session_state.company_details["valid_till"] = valid_till_date

with col_b:
    st.info(f"✅ Quotation will be valid until: **{valid_till_date}**")

# ======================
# ✏ EDIT / VIEW TERMS & CONDITIONS (Admin & Buyer)
# ======================
st.markdown("---")

# Show different button based on role
if st.session_state.role == "admin":
    if st.button("📝 Edit Terms & Conditions"):
        st.session_state.show_edit_terms = True
elif st.session_state.role == "buyer":
    if st.button("📄 View Terms & Conditions"):
        st.session_state.show_edit_terms = True

if st.button("📅 Generate PDF Quotation") and output_data:
    # Block generating PDF until Terms & Conditions are reviewed
    if not st.session_state.get("terms_reviewed", False):
        st.warning("⚠ Please review the Terms & Conditions first.")
        st.session_state.show_edit_terms = True
        st.stop()

    with st.spinner("Generating PDF"):
        st.session_state.pdf_data = output_data
        data_str = str(output_data) + str(final_total) + str(company_details)
        data_hash = hashlib.md5(data_str.encode()).hexdigest()
        pdf_filename = f"{company_details['company_name']}{datetime.now().strftime('%Y%m%d%H%M')}.pdf"
        pdf_file = build_pdf_cached(data_hash, final_total, company_details)
        
        # 👉 Prepare record
        new_record = {
            "user_email": st.session_state.user_email,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "company_name": company_details["company_name"],
            "contact_person": company_details["contact_person"],
            "total": round(final_total, 2),
            "items": output_data.copy(),
            "pdf_filename": pdf_filename,
            "quotation_hash": data_hash
        }
        
        # 👉 Save to session state
        st.session_state.history.append(new_record)
        
        # 👉 Save to Google Sheet
        history_sheet = get_history_sheet()
        if history_sheet:
            try:
                import json
                row = [
                    new_record["user_email"],
                    new_record["timestamp"],
                    new_record["company_name"],
                    new_record["contact_person"],
                    new_record["total"],
                    json.dumps(new_record["items"]),
                    new_record["pdf_filename"],
                    new_record["quotation_hash"]
                ]
                history_sheet.append_row(row)
                st.success("✅ Quotation saved to session and Google Sheet!")
            except Exception as e:
                st.warning(f"⚠ Saved locally, but failed to save to Google Sheet: {e}")
        else:
            st.warning("⚠ Could not connect to Google Sheet. Quotation saved locally only.")
        
        history_sheet = get_history_sheet()
        if history_sheet:
            st.session_state.history = load_user_history_from_sheet(st.session_state.user_email, history_sheet)
            st.success("✅ History refreshed from Google Sheet!")
        else:
            st.error("Failed to connect to Google Sheets.")
        
        # Offer download
        with open(pdf_file, "rb") as f:
            st.download_button(
                label="⬇ Click to Download PDF",
                data=f,
                file_name=pdf_filename,
                mime="application/pdf",
                key=f"download_pdf_{data_hash}"

            )






