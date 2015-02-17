#!/usr/bin/env python

import mosquitto
import time
                                
# Start MQTT (Mosquitto)
mqttc = mosquitto.Mosquitto()
mqttc.connect("127.0.0.1",1883, 60, True)

if __name__ == '__main__':    
    while True:
        mqttc.publish("tx","100,200,300,s")
        print "tx 100,200,300,s"
        time.sleep(5.0)
