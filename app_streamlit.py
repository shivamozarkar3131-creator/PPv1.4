import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import streamlit.components.v1 as components
from sr_core import analyze, SRConfig
from streamlit_autorefresh import st_autorefresh
import requests
import yaml
from yaml.loader import SafeLoader
import firebase_admin
from firebase_admin import credentials, db
import os

# --------- Firebase Connection ---------
def get_firebase_cred():
    import tempfile
    import json as pyjson
    key_path = None
    if "firebase_key" in st.secrets:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as tf:
            tf.write(pyjson.dumps(dict(st.secrets["firebase_key"])))
            key_path = tf.name
        return key_path
    else:
        return "firebase-key.json"

FIREBASE_URL = st.secrets.firebase_key.firebase_url if "firebase_key" in st.secrets else "YOUR_FIREBASE_DB_URL_HERE"
cred_path = get_firebase_cred()
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_URL
    })

# Firebase functions for user credentials
def load_all_users():
    """Load all users from Firebase"""
    ref = db.reference("credentials/usernames")
    data = ref.get()
    return data if data else {}

def save_user_to_firebase(username, user_data):
    """Save a single user to Firebase"""
    ref = db.reference(f"credentials/usernames/{username}")
    ref.set(user_data)

# Firebase functions for watchlists
def load_user_watchlist(username, default=None):
    ref = db.reference(f"watchlists/{username}")
    data = ref.get()
    return data if isinstance(data, list) and data else (default or [])

def save_user_watchlist(username, watchlist):
    ref = db.reference(f"watchlists/{username}")
    ref.set(watchlist)

# Firebase functions for user Telegram settings
def load_user_telegram(username):
    """Load user's Telegram chat_id from Firebase"""
    ref = db.reference(f"user_settings/{username}/telegram_chat_id")
    return ref.get()

def save_user_telegram(username, chat_id):
    """Save user's Telegram chat_id to Firebase"""
    ref = db.reference(f"user_settings/{username}/telegram_chat_id")
    ref.set(chat_id)

# --------- Load Config (Hybrid: YAML + Firebase) ---------
CONFIG_PATH = 'credentials.yaml'
with open(CONFIG_PATH) as file:
    config = yaml.load(file, Loader=SafeLoader)

# Load users from Firebase (overrides YAML users for persistence)
firebase_users = load_all_users()
if firebase_users:
    config['credentials']['usernames'] = firebase_users

# --------- Authentication ---------
import streamlit_authenticator as stauth

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

try:
    authenticator.login('main')
except Exception as e:
    st.error(e)

# -------- Registration --------
if not st.session_state.get('authentication_status'):
    try:
        email_of_registered_user, username_of_registered_user, name_of_registered_user = authenticator.register_user()
        if email_of_registered_user:
            st.success('User registered successfully. You can now log in!')
            
            # Save to Firebase (persistent across restarts)
            user_data = config['credentials']['usernames'][username_of_registered_user]
            save_user_to_firebase(username_of_registered_user, user_data)
            
            # Optionally save to local YAML for dev/backup
            with open(CONFIG_PATH, 'w') as file:
                yaml.dump(config, file, default_flow_style=False, allow_unicode=True)
    except Exception as e:
        st.error(e)

# ---- Main app ----
if st.session_state.get('authentication_status'):
    authenticator.logout('Logout', 'sidebar')
    st.success(f"Welcome {st.session_state.get('name')}")

    st.set_page_config(page_title="S/R with RSI, MACD & Volume", layout="wide")
    st.title("📈 Support & Resistance + RSI & MACD + Volume Confirmation + Trading Signals")

    refresh_count = st_autorefresh(interval=30_000, key="live_refresh")
    st.sidebar.write(f"🔄 Auto-refresh count: {refresh_count}")

    tab = st.sidebar.radio("Select View", ["Home", "Watchlist"])

    # Firebase-Backed Watchlist
    username = st.session_state.get("username")
    default_watchlist = [
        "TATAMOTORS.NS", "IDFCFIRSTB.NS", "WIPRO.NS",
        "NBCC.NS", "ZENSARTECH.NS", "EPL.NS",
        "BERGEPAINT.NS", "RECLTD.NS", "AARON.NS"
    ]
    if "watchlist" not in st.session_state:
        st.session_state.watchlist = load_user_watchlist(username, default=default_watchlist)

    st.sidebar.subheader("Manage Watchlist")
    st.sidebar.write("Current Watchlist:")
    for sym in st.session_state.watchlist:
        st.sidebar.write(f"- {sym}")

    new_symbol = st.sidebar.text_input("Add Symbol (e.g., HDFCBANK.NS)")
    if st.sidebar.button("Add Symbol"):
        new_symbol_clean = new_symbol.upper().strip()
        if new_symbol_clean == "":
            st.sidebar.error("Enter a symbol.")
        elif new_symbol_clean in st.session_state.watchlist:
            st.sidebar.warning("Symbol already added.")
        else:
            st.session_state.watchlist.append(new_symbol_clean)
            save_user_watchlist(username, st.session_state.watchlist)
            st.success(f"Added {new_symbol_clean} to your watchlist!")

    remove_symbol = st.sidebar.selectbox("Remove Symbol", [""] + st.session_state.watchlist)
    if st.sidebar.button("Remove Symbol"):
        if remove_symbol and remove_symbol in st.session_state.watchlist:
            st.session_state.watchlist.remove(remove_symbol)
            save_user_watchlist(username, st.session_state.watchlist)
            st.warning(f"Removed {remove_symbol} from watchlist.")

    # ---- Personal Telegram Settings ----
    st.sidebar.subheader("🔔 Personal Telegram Alerts")
    user_telegram_chat_id = load_user_telegram(username)

    if user_telegram_chat_id:
        st.sidebar.success(f"✅ Telegram connected: {user_telegram_chat_id}")
    else:
        st.sidebar.info("Set your Telegram Chat ID to receive personal alerts.")

    new_chat_id = st.sidebar.text_input("Your Telegram Chat ID", value=user_telegram_chat_id or "")
    if st.sidebar.button("Save Telegram Chat ID"):
        if new_chat_id.strip():
            save_user_telegram(username, new_chat_id.strip())
            st.sidebar.success("Telegram Chat ID saved!")
        else:
            st.sidebar.error("Please enter a valid Chat ID.")

    st.sidebar.subheader("Analysis Options")
    symbol_input = st.sidebar.text_input("Stock Symbol for Home", "RELIANCE.NS")
    period = st.sidebar.selectbox("Select Period", ["1mo", "3mo", "6mo", "1y", "2y"])
    interval = st.sidebar.selectbox("Select Interval", ["5m", "15m", "30m", "1h", "2h", "1d"])
    distance = st.sidebar.number_input("SR Distance", min_value=1, max_value=50, value=5, step=1)
    tolerance = st.sidebar.number_input("SR Tolerance", min_value=0.001, max_value=0.05, value=0.01, step=0.001)

    st.sidebar.subheader("Indicator Options")
    show_rsi = st.sidebar.checkbox("Show RSI Chart", value=True)
    show_macd = st.sidebar.checkbox("Show MACD Chart", value=True)
    enable_sound_alert = st.sidebar.checkbox("Enable Sound Alerts", value=False)
    enable_email_alert = st.sidebar.checkbox("Enable Email Alerts", value=False)

    telegram_token = st.secrets.get("telegram_token", "")
    email_sender = st.secrets.get("email_sender", "")
    email_password = st.secrets.get("email_password", "")
    email_receiver = st.secrets.get("email_receiver", "")

    st.sidebar.subheader("📲 Test Telegram Alert")
    if st.sidebar.button("Send Test Telegram Alert"):
        if telegram_token and user_telegram_chat_id:
            try:
                url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                payload = {"chat_id": user_telegram_chat_id, "text": "✅ Telegram alert test successful!"}
                requests.post(url, data=payload)
                st.sidebar.success("Test Telegram alert sent successfully!")
            except Exception as e:
                st.sidebar.error(f"Failed to send test alert: {e}")
        else:
            st.sidebar.warning("Please set your Telegram Chat ID and bot token in secrets!")

    st.sidebar.subheader("Indicator Parameters")
    rsi_period = st.sidebar.number_input("RSI Period", min_value=5, max_value=50, value=14, step=1)
    macd_fast = st.sidebar.number_input("MACD Fast EMA", min_value=5, max_value=50, value=12, step=1)
    macd_slow = st.sidebar.number_input("MACD Slow EMA", min_value=10, max_value=100, value=26, step=1)
    macd_signal = st.sidebar.number_input("MACD Signal EMA", min_value=5, max_value=30, value=9, step=1)

    st.sidebar.subheader("Volume Confirmation")
    enable_volume_filter = st.sidebar.checkbox("Enable Volume Confirmation for Signals", value=True)

    st.markdown(
        """
        <style>
        @keyframes blink { 0% { background-color: inherit; } 50% { background-color: yellow; } 100% { background-color: inherit; } }
        .blink { animation: blink 1s linear 2; }
        #popup-alert {
            position: fixed; top: 20px; right: 20px;
            background-color: #ffcc00; color: black; padding: 15px;
            border-radius: 10px; box-shadow: 2px 2px 10px rgba(0,0,0,0.3);
            z-index: 9999; font-weight: bold; display: none;
        }
        </style>
        <script>
        function showPopup(message) {
            var popup = document.getElementById('popup-alert');
            popup.innerText = message;
            popup.style.display = 'block';
            setTimeout(function() { popup.style.display = 'none'; }, 5000);
        }
        </script>
        <div id="popup-alert"></div>
        """, unsafe_allow_html=True
    )

    def send_email_alert(subject: str, body: str, from_email: str, password: str, to_email: str) -> None:
        from email.mime.text import MIMEText
        import smtplib
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email
        try:
            with smtplib.SMTP('smtp.office365.com', 587) as server:
                server.starttls()
                server.login(from_email, password)
                server.send_message(msg)
                st.success(f"✅ Email sent to {to_email}")
        except Exception as e:
            st.error(f"Email send failed: {e}")

    def send_telegram_alert(message: str, token: str, chat_id: str) -> None:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": message}
            requests.post(url, data=payload)
            st.success("📲 Telegram alert sent!")
        except Exception as e:
            st.error(f"Telegram send failed: {e}")

    @st.cache_data(show_spinner=False)
    def get_analysis(symbol, period, interval, _cfg, rsi_period, macd_fast, macd_slow, macd_signal, use_volume):
        return analyze(
            symbol=symbol, period=period, interval=interval, cfg=_cfg,
            rsi_period=rsi_period, macd_fast=macd_fast, macd_slow=macd_slow,
            macd_signal=macd_signal, use_volume=use_volume
        )

    def show_stock(symbol: str, hide_sr: bool = False):
        st.subheader(f"🔹 {symbol}")
        _cfg = SRConfig(distance=distance, tolerance=tolerance, min_touches=2)
        try:
            sr, df, signals = get_analysis(
                symbol, period, interval, _cfg,
                rsi_period, macd_fast, macd_slow, macd_signal,
                enable_volume_filter
            )

            if "last_alert" not in st.session_state:
                st.session_state.last_alert = {}
            if symbol not in st.session_state.last_alert:
                st.session_state.last_alert[symbol] = None

            if not hide_sr:
                st.write("📊 Support & Resistance Levels")
                st.dataframe(pd.DataFrame(sr))

            st.write("🚨 Live Alerts")
            for sig in signals:
                alert_text = f"{sig['signal']} Signal! Price: {sig['price']}\nReason: {sig['reason']}"
                if sig.get("Volume"):
                    alert_text += f"\nVolume: {sig['Volume']:.0f}"

                is_new = sig['signal'] != st.session_state.last_alert.get(symbol)
                color = "green" if sig["signal"] == "BUY" else "red" if sig["signal"] == "SELL" else "blue"

                if is_new:
                    st.markdown(
                        f"<div class='blink' style='color:{color}; padding:10px; border-radius:5px; background-color:white'>{alert_text}</div>",
                        unsafe_allow_html=True
                    )

                    if sig["signal"] in ["BUY", "SELL"]:
                        components.html(f"<script>showPopup('{alert_text}');</script>", height=0)
                        if enable_sound_alert:
                            components.html("""<audio autoplay><source src="https://www.soundjay.com/buttons/sounds/beep-07.mp3" type="audio/mpeg"></audio>""", height=0)

                        if enable_email_alert and email_sender and email_password and email_receiver:
                            send_email_alert(
                                subject=f"{sig['signal']} Alert for {symbol}", body=alert_text,
                                from_email=email_sender, password=email_password, to_email=email_receiver
                            )
                        
                        # Send to user's personal Telegram
                        if telegram_token and user_telegram_chat_id:
                            send_telegram_alert(
                                f"📊 v1.1 🚨 {sig['signal']} Alert for {symbol}\n⏳ Period: {period}, Interval: {interval}\n{alert_text}",
                                telegram_token,
                                user_telegram_chat_id
                            )
                    st.session_state.last_alert[symbol] = sig['signal']
                else:
                    st.markdown(
                        f"<div style='color:gray; padding:10px; border-radius:5px; background-color:#f5f5f5'>{alert_text}</div>",
                        unsafe_allow_html=True
                    )

            if not hide_sr:
                if show_rsi:
                    st.subheader("📊 RSI Indicator")
                    fig_rsi = go.Figure()
                    fig_rsi.add_trace(go.Scatter(x=df.index, y=df["RSI"], mode="lines", name="RSI"))
                    fig_rsi.add_hline(y=70, line_dash="dot", line_color="red")
                    fig_rsi.add_hline(y=30, line_dash="dot", line_color="green")
                    st.plotly_chart(fig_rsi, use_container_width=True)

                if show_macd:
                    st.subheader("📊 MACD Indicator")
                    fig_macd = go.Figure()
                    fig_macd.add_trace(go.Scatter(x=df.index, y=df["MACD"], mode="lines", name="MACD"))
                    fig_macd.add_trace(go.Scatter(x=df.index, y=df["MACD_Signal"], mode="lines", name="Signal"))
                    st.plotly_chart(fig_macd, use_container_width=True)

        except Exception as e:
            st.error(f"Error fetching {symbol}: {e}")

    if tab == "Home":
        selected_stock = st.sidebar.selectbox("Select a stock from watchlist", st.session_state.watchlist)
        if selected_stock:
            show_stock(selected_stock, hide_sr=False)
    else:
        st.subheader("📢 Watchlist Live Alerts Only")
        for sym in st.session_state.watchlist:
            show_stock(sym, hide_sr=True)

elif st.session_state.get('authentication_status') == False:
    st.error('Username/password is incorrect')
elif st.session_state.get('authentication_status') is None:
    st.warning('Please enter your username and password')
