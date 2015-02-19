#!/usr/bin/env python

import mosquitto
import time
                                
# Start MQTT (Mosquitto)
mqttc = mosquitto.Mosquitto()
mqttc.connect("127.0.0.1",1883, 60, True)

if __name__ == '__main__':    
    while True:
        mqttc.publish("emonhub/tx/30/values","1,19.5")
        time.sleep(5.0)
        mqttc.publish("emonhub/tx/30/values","0,19.2")
        time.sleep(5.0)
