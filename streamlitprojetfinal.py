import os
import time
import threading
from datetime import datetime

import streamlit as st
import pandas as pd
import paho.mqtt.client as mqtt


# ==========================
# CONFIG
# ==========================
DEFAULT_HOST = os.getenv("MQTT_HOST", "51.103.121.129")
DEFAULT_PORT = int(os.getenv("MQTT_PORT", "1883"))

# Temp (fixe)
TOPIC_TEMP = "esp32_1/temp"

# 👉 Humidité (on va l'afficher dans la case "Luminosité (%)")
TOPIC_HUM = "esp32_1/humidity"

MAX_POINTS = 200


# ==========================
# UTILS
# ==========================
def to_float(x):
    try:
        return float(str(x).replace(",", "."))
    except Exception:
        return None

def to_int(x):
    try:
        return int(float(str(x).replace(",", ".")))
    except Exception:
        return None

def pick_first_topic(data: dict, candidates: list[str]):
    """Retourne (topic, payload) pour le premier topic trouvé dans data."""
    for t in candidates:
        if t in data:
            return t, data.get(t)
    return None, None


# ==========================
# MQTT STATE (thread-safe)
# ==========================
class MqttState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        self.last_ts = {}
        self.connected = False
        self.last_error = None

    def set_connected(self, v: bool):
        with self.lock:
            self.connected = v

    def set_error(self, err: str):
        with self.lock:
            self.last_error = err

    def put(self, topic: str, payload: str):
        with self.lock:
            self.data[topic] = payload
            self.last_ts[topic] = datetime.now()

    def snapshot(self):
        with self.lock:
            return dict(self.data), dict(self.last_ts), self.connected, self.last_error


@st.cache_resource
def start_mqtt_client(host: str, port: int):
    state = MqttState()

    def on_connect(client, userdata, flags, rc, properties=None):
        ok = (rc == 0)
        state.set_connected(ok)
        if ok:
            client.subscribe("#")
        else:
            state.set_error(f"MQTT connect rc={rc}")

    def on_disconnect(client, userdata, rc, properties=None):
        state.set_connected(False)
        if rc != 0:
            state.set_error(f"MQTT disconnect rc={rc}")

    def on_message(client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")
        state.put(msg.topic, payload)

    client = mqtt.Client(protocol=mqtt.MQTTv311)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        client.connect(host, port, keepalive=60)
        client.loop_start()
    except Exception as e:
        state.set_error(str(e))

    return client, state


# ==========================
# UI
# ==========================
st.set_page_config(page_title="Dashboard ESP32", layout="wide")
st.title("PROJET FINAL 304_311")
st.title("Treamlit(MQTT)")

with st.sidebar:
    st.header("Connexion MQTT")
    host = st.text_input("Host", value=DEFAULT_HOST)
    port = st.number_input("Port", min_value=1, max_value=65535, value=DEFAULT_PORT, step=1)

    if st.button("Reconnect MQTT"):
        st.cache_resource.clear()
        st.rerun()

    st.divider()
    st.header("Rafraîchissement")
    auto_refresh = st.toggle("Auto-refresh", value=False)
    refresh_sec = st.slider("Période refresh (sec)", 1, 10, 2)

# MQTT
client, state = start_mqtt_client(host, int(port))
data, last_ts, is_connected, last_error = state.snapshot()

if last_error:
    st.warning(f"MQTT: {host}:{port} — erreur: {last_error}")
else:
    st.caption(f"MQTT: {host}:{port} — {'Connecté' if is_connected else 'Non connecté / en attente'}")


# ==========================
# VALUES
# ==========================
temperature = to_float(data.get(TOPIC_TEMP))

# Humidité: on accepte plusieurs topics possibles
HUM_CANDIDATES = [
    TOPIC_HUM,                 # esp32_1/humidity
    "esp32/sensors/humidity",  # courant
    "esp32_1/hum",
    "esp32/humidity",
]

hum_topic_used, hum_payload = pick_first_topic(data, HUM_CANDIDATES)
humidity = to_int(hum_payload)  # ou to_float(hum_payload) si tu veux des décimales

# KPI
c1, c2, c3 = st.columns(3)
c1.metric("Température (°C)", "-" if temperature is None else f"{temperature:.1f}")

# ✅ Afficher l'humidité dans la case "Luminosité (%)"
c2.metric("Luminosité (%)", "-" if humidity is None else str(humidity))

# Dernière réception (temp + humidity affichée comme L)
t_ts = last_ts.get(TOPIC_TEMP)
l_ts = last_ts.get(hum_topic_used) if hum_topic_used else None
c3.metric(
    "Dernière réception",
    "-" if (t_ts is None and l_ts is None) else f"T:{t_ts.strftime('%H:%M:%S') if t_ts else '-'} / L:{l_ts.strftime('%H:%M:%S') if l_ts else '-'}"
)

st.divider()


# ==========================
# HISTORY + GRAPHS
# ==========================
if "history" not in st.session_state:
    st.session_state["history"] = []

now = datetime.now()
if temperature is not None or humidity is not None:
    st.session_state["history"].append({
        "time": now,
        "temperature": temperature,
        "humidity": humidity,  # on stocke bien humidity
    })
    st.session_state["history"] = st.session_state["history"][-MAX_POINTS:]

hist_df = pd.DataFrame(st.session_state["history"])
if not hist_df.empty:
    hist_df = hist_df.set_index("time")

colA, colB = st.columns(2)

with colA:
    st.subheader("Courbe Température")
    if hist_df.empty:
        st.info("En attente de données...")
    else:
        st.line_chart(hist_df["temperature"])

with colB:
    # ✅ On garde le titre "Luminosité" mais on trace l'humidité
    st.subheader("Courbe Luminosité")
    if hist_df.empty:
        st.info("En attente de données...")
    else:
        st.line_chart(hist_df["humidity"])

st.divider()


# ==========================
# MQTT DETAILS
# ==========================
with st.expander("Détails MQTT (topics reçus)", expanded=False):
    rows = []
    for topic, payload in sorted(data.items()):
        ts = last_ts.get(topic)
        rows.append({
            "topic": topic,
            "payload": payload,
            "reçu à": ts.strftime("%H:%M:%S") if ts else "-"
        })
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("Aucun message reçu pour l’instant.")
    else:
        st.dataframe(df, width="stretch", hide_index=True)

# Auto-refresh
if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()
