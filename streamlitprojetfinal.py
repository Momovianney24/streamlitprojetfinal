import os
import time
import threading
from datetime import datetime

import streamlit as st
import pandas as pd
import paho.mqtt.client as mqtt

# (Optionnel) DB
from sqlalchemy import create_engine, text

# -----------------------------
# CONFIG (à adapter)
# -----------------------------
MQTT_HOST = os.getenv("MQTT_HOST", "51.103.121.129")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

# Topics (ton code ESP32_1)
TOPICS_DEFAULT = [
    ("esp32/sensors/temperature", 0),
    ("esp32/sensors/luminosity", 0),
    ("esp32/state/servo", 0),
    ("esp32/state/buzzer", 0),
    ("esp32_1/ldr", 0),            # publié sur changement d'état dans ton code
    ("esp32_1/servo", 0),          # topic de commande (on peut aussi s'y abonner)
]

# Topics (Node2 selon ta capture)
TOPICS_NODE2 = [
    ("esp32/Node2/sensors/temperature", 0),
    ("esp32/Node2/sensors/luminosity", 0),
]

SERVO_COMMAND_TOPIC = "esp32_1/servo"

# MariaDB (optionnel)
DB_ENABLED = os.getenv("DB_ENABLED", "0") == "1"
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "iot")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "")

# Exemple URL SQLAlchemy avec PyMySQL:
# mysql+pymysql://user:pass@host:port/dbname
DB_URL = os.getenv(
    "DB_URL",
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# -----------------------------
# MQTT MANAGER (thread-safe)
# -----------------------------
class MqttState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}   # topic -> value (string)
        self.last_ts = {}  # topic -> datetime

    def set(self, topic: str, value: str):
        with self.lock:
            self.data[topic] = value
            self.last_ts[topic] = datetime.now()

    def snapshot(self):
        with self.lock:
            return dict(self.data), dict(self.last_ts)


@st.cache_resource
def start_mqtt(topics):
    state = MqttState()

    def on_connect(client, userdata, flags, rc, properties=None):
        # rc == 0 => OK
        for t, qos in topics:
            client.subscribe(t, qos=qos)

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = str(msg.payload)
        state.set(msg.topic, payload)

    client = mqtt.Client(protocol=mqtt.MQTTv311)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

    # boucle réseau dans un thread
    client.loop_start()

    return client, state


def parse_float(x):
    try:
        return float(x)
    except Exception:
        return None


def parse_int(x):
    try:
        return int(float(x))
    except Exception:
        return None


# -----------------------------
# DB (optionnel)
# -----------------------------
@st.cache_resource
def get_engine():
    if not DB_ENABLED:
        return None
    try:
        return create_engine(DB_URL, pool_pre_ping=True)
    except Exception:
        return None


def load_history(engine, limit=200):
    if engine is None:
        return None
    try:
        q = text(f"""
            SELECT id, date, temperature, luminosity
            FROM mesures_capteurs
            ORDER BY id DESC
            LIMIT :limit
        """)
        df = pd.read_sql(q, engine, params={"limit": limit})
        # remise dans l'ordre chronologique
        df = df.sort_values("id")
        return df
    except Exception as e:
        return e


# -----------------------------
# UI STREAMLIT
# -----------------------------
st.set_page_config(page_title="ESP32 Dashboard", layout="wide")

st.title("Dashboard ESP32 (MQTT + MariaDB optionnel)")

with st.sidebar:
    st.header("Connexion")
    st.write(f"MQTT: `{MQTT_HOST}:{MQTT_PORT}`")

    source = st.selectbox("Source capteurs", ["ESP32_1 (esp32/...)", "Node2 (esp32/Node2/...)"])

    auto_refresh = st.toggle("Auto-refresh", value=True)
    refresh_sec = st.slider("Période refresh (sec)", 1, 10, 2)

    st.divider()
    st.header("Commande Servo (ESP32_1)")
    angle = st.slider("Angle servo", 0, 180, 0)
    send = st.button("Envoyer l'angle")

# Choix topics selon source
topics = TOPICS_DEFAULT if source.startswith("ESP32_1") else (TOPICS_NODE2 + TOPICS_DEFAULT)

client, state = start_mqtt(tuple(topics))

# Envoyer commande servo
if send:
    client.publish(SERVO_COMMAND_TOPIC, str(angle))
    st.sidebar.success(f"Commande envoyée: {SERVO_COMMAND_TOPIC} = {angle}")

# Snapshot données MQTT
data, last_ts = state.snapshot()

# Choix des clés selon source
if source.startswith("ESP32_1"):
    t_key = "esp32/sensors/temperature"
    l_key = "esp32/sensors/luminosity"
else:
    t_key = "esp32/Node2/sensors/temperature"
    l_key = "esp32/Node2/sensors/luminosity"

temp_raw = data.get(t_key)
lum_raw = data.get(l_key)

temp = parse_float(temp_raw) if temp_raw is not None else None
lum = parse_int(lum_raw) if lum_raw is not None else None

servo_state = data.get("esp32/state/servo")
buzzer_state = data.get("esp32/state/buzzer")
ldr_state = data.get("esp32_1/ldr")

# --- KPI en haut
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Température (°C)", "-" if temp is None else f"{temp:.1f}", help=t_key)
c2.metric("Luminosité (%)", "-" if lum is None else f"{lum}", help=l_key)
c3.metric("Servo (state)", "-" if servo_state is None else servo_state)
c4.metric("Buzzer (state)", "-" if buzzer_state is None else buzzer_state)
c5.metric("LDR (switch)", "-" if ldr_state is None else ldr_state)

# --- Dernières dates de réception
with st.expander("Détails MQTT (topics reçus)"):
    rows = []
    for k, v in sorted(data.items()):
        ts = last_ts.get(k)
        rows.append({
            "topic": k,
            "payload": v,
            "reçu à": ts.strftime("%H:%M:%S") if ts else "-"
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# --- Historique MariaDB (optionnel)
st.subheader("Historique (MariaDB)")

engine = get_engine()
if not DB_ENABLED:
    st.info("DB désactivée. Pour activer : définis DB_ENABLED=1 et les variables DB_* (ou DB_URL).")
else:
    hist = load_history(engine, limit=300)
    if isinstance(hist, Exception):
        st.error(f"Erreur DB: {hist}")
    elif hist is None:
        st.warning("Impossible d'initialiser la connexion DB.")
    else:
        st.dataframe(hist, use_container_width=True, hide_index=True)
        # Graphs
        if "date" in hist.columns:
            hist_plot = hist.copy()
            hist_plot["date"] = pd.to_datetime(hist_plot["date"], errors="coerce")
            hist_plot = hist_plot.dropna(subset=["date"]).set_index("date")

            colA, colB = st.columns(2)
            with colA:
                st.line_chart(hist_plot["temperature"])
            with colB:
                st.line_chart(hist_plot["luminosity"])

# Auto-refresh
if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()
