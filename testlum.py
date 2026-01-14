import paho.mqtt.client as mqtt
import time

HOST="51.103.121.129"
PORT=1883

def on_connect(client, userdata, flags, rc):
    print("connected rc=", rc)
    client.subscribe("#")

def on_message(client, userdata, msg):
    if "lum" in msg.topic.lower() or "ldr" in msg.topic.lower():
        print(msg.topic, "=>", msg.payload.decode(errors="ignore"))

c = mqtt.Client()
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 60)
c.loop_start()

print("listening 20s...")
time.sleep(20)
print("done")
