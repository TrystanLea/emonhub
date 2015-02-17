#!/usr/bin/env python

# Example emonhub frame reader for external application using frame's directly

import mosquitto
import time

def on_message(mosq, obj, msg):
    print msg.topic + " " + msg.payload
                                
# Start MQTT (Mosquitto)
mqttc = mosquitto.Mosquitto()
mqttc.on_message = on_message
mqttc.connect("127.0.0.1",1883, 60, True)
mqttc.subscribe("nodes/#", 0)

if __name__ == '__main__':    
    while True:
        mqttc.loop(0)
        time.sleep(0.1)
