import time
import paho.mqtt.client as mqtt

HOST = "51.103.121.129"
PORT = 1883

def on_connect(client, userdata, flags, rc):
    print("CONNECTED rc =", rc)
    client.subscribe("#")
    print("SUBSCRIBED #")

def on_message(client, userdata, msg):
    print(msg.topic, "=>", msg.payload.decode("utf-8", errors="ignore"))

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(HOST, PORT, 60)
client.loop_start()

print("Listening 20s...")
time.sleep(20)
print("Done.")
