import os
import time
import json
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

TOPIC_MUTE_ALARM = "esp32/alarm/mute"
TOPIC_SEUIL = "esp32_1/seuil"

MAX_POINTS = 200  # points gardés pour les graphes


# ==========================
# OUTILS
# ==========================
def to_float(x):
    try:
        return float(x)
    except Exception:
        return None

def to_int(x):
    try:
        return int(float(x))
    except Exception:
        return None

def parse_json_safe(s):
    try:
        return json.loads(s)
    except Exception:
        return None


# ==========================
# ETAT MQTT (thread-safe)
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
        state.set_connected(rc == 0)
        if rc == 0:
            client.subscribe("#")  # écoute tous les topics
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
st.title("Dashboard ESP32 (MQTT)")

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

    st.divider()
    st.header("Commandes")
    if st.button("Mute alarm"):
        # mute = 1
        # (si ton système attend 0/1 tu peux garder)
        # sinon dis-moi et j'adapte
        st.session_state["_do_mute"] = True

    seuil = st.number_input("Seuil", min_value=0.0, max_value=100.0, value=20.0, step=0.5)
    if st.button("Envoyer seuil"):
        st.session_state["_do_seuil"] = str(seuil)


client, state = start_mqtt_client(host, int(port))

# Exécuter les commandes MQTT (depuis session_state)
if st.session_state.get("_do_mute"):
    client.publish(TOPIC_MUTE_ALARM, "1")
    st.session_state["_do_mute"] = False

if st.session_state.get("_do_seuil") is not None:
    client.publish(TOPIC_SEUIL, st.session_state["_do_seuil"])
    st.session_state["_do_seuil"] = None


data, last_ts, is_connected, last_error = state.snapshot()

if last_error:
    st.warning(f"MQTT: {host}:{port} — erreur: {last_error}")
else:
    st.caption(f"MQTT: {host}:{port} — {'Connecté' if is_connected else 'Non connecté / en attente'}")


# ==========================
# EXTRACTION DES VALEURS
# ==========================
capteur_json = parse_json_safe(data.get("capteur/data", "")) if "capteur/data" in data else None

# Température
temperature = to_float(data.get("esp32_1/temp"))
if temperature is None and isinstance(capteur_json, dict):
    temperature = to_float(capteur_json.get("temperature"))

# Luminosité (priorité à un topic dédié si tu en as, sinon pot)
# 1) si tu as un topic luminosity quelque part, mets-le ici
luminosity = None
for candidate_topic in [
    "esp32/sensors/luminosity",
    "esp32_1/luminosity",
    "esp32_1/ldr",
]:
    if candidate_topic in data:
        luminosity = to_int(data.get(candidate_topic))
        break

# 2) fallback: pot dans capteur/data
if luminosity is None and isinstance(capteur_json, dict):
    luminosity = to_int(capteur_json.get("pot"))

# Alarm / IR / Status / Seuil
alarm_raw = data.get("esp32_1/alarm")
alarm = to_int(alarm_raw) if alarm_raw is not None else None
if alarm is None and isinstance(capteur_json, dict):
    a = capteur_json.get("alarm")
    alarm = 1 if a is True else 0 if a is False else to_int(a)

ir = to_int(data.get("esp32_1/ir"))
status = data.get("esp32_1/status") or "-"

seuil_raw = data.get("esp32_1/seuil")
seuil_val = to_float(seuil_raw) if seuil_raw is not None else None
if seuil_val is None and isinstance(capteur_json, dict):
    seuil_val = to_float(capteur_json.get("seuil"))

flame = None
if isinstance(capteur_json, dict):
    flame = to_int(capteur_json.get("flame"))


# ==========================
# KPI
# ==========================
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

c1.metric("Température (°C)", "-" if temperature is None else f"{temperature:.1f}")
c2.metric("Luminosité (%)", "-" if luminosity is None else str(luminosity))
c3.metric("Seuil", "-" if seuil_val is None else f"{seuil_val:g}")
c4.metric("Flame", "-" if flame is None else str(flame))
c5.metric("Alarm", "-" if alarm is None else ("ON" if alarm == 1 else "OFF"))
c6.metric("IR", "-" if ir is None else str(ir))
c7.metric("Status", status)

st.divider()

# ==========================
# GRAPHES (historique en mémoire)
# ==========================
if "history" not in st.session_state:
    st.session_state["history"] = []

# Ajout d'un point si on a au moins temp ou luminosité
now = datetime.now()
if temperature is not None or luminosity is not None:
    st.session_state["history"].append({
        "time": now,
        "temperature": temperature,
        "luminosity": luminosity,
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
    st.subheader("Courbe Luminosité")
    if hist_df.empty:
        st.info("En attente de données...")
    else:
        st.line_chart(hist_df["luminosity"])

st.divider()

# ==========================
# DETAILS MQTT
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

# Auto refresh
if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()
