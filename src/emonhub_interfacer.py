"""

  This code is released under the GNU Affero General Public License.
  
  OpenEnergyMonitor project:
  http://openenergymonitor.org

"""

import serial
import time
import datetime
import logging
import socket
import select
import threading
import urllib2
import json
import paho.mqtt.client as mqtt
import uuid
import traceback

import emonhub_coder as ehc
import emonhub_buffer as ehb

"""class EmonHubInterfacer

Monitors a data source. 

This almost empty class is meant to be inherited by subclasses specific to
their data source.

"""
def log_exceptions_from_class_method(f):
    def wrapper(*args):
        self=args[0]
        try:
            return f(*args)
        except:
            self._log.warning("Exception caught in "+self.name+" thread. "+traceback.format_exc())
            return
    return wrapper

class EmonHubInterfacer(threading.Thread):

    def __init__(self, name):
        
        # Initialize logger
        self._log = logging.getLogger("EmonHub")

        # Initialise thread
        threading.Thread.__init__(self)
        self.setName(name)

        # Initialise settings
        self._sub_channels = {}
        self._pub_channels = {}
        self.init_settings = {}
        self._defaults = {'pause': 'off', 'interval': 0, 'datacode': '0', 'batchsize': '1',
                          'scale':'1', 'timestamped': False, 'targeted': False,
                          'pubchannels':["ch1"],'subchannels':["ch2"], 'nodeoffset' : '0'}
        self._settings = {}

        # This line will stop the default values printing to logfile at start-up
        # unless they have been overwritten by emonhub.conf entries
        # comment out if diagnosing a startup value issue
        self._settings.update(self._defaults)

        # Initialize interval timer's "started at" timestamp
        self._interval_timestamp = 0
        
        buffer_type = "memory"
        buffer_size = 1000

        # Create underlying buffer implementation
        self.buffer = ehb.getBuffer(buffer_type)(name, buffer_size)

        # set an absolute upper limit for number of items to process per post
        # number of items posted is the lower of this item limit, buffer_size, or the
        # batchsize, as set in reporter settings or by the default value.
        self._item_limit = buffer_size
        
        # create a stop
        self.stop = False

    @log_exceptions_from_class_method
    def run(self):
        """
        Run the interfacer.
        Any regularly performed tasks actioned here along with passing received values

        """

        while not self.stop:
            # Read the input and process data if available
            rxc = self.read()
            # if 'pause' in self._settings and \
            #                 str.lower(self._settings['pause']) in ['all', 'in']:
            #     pass
            # else:
            if rxc:
                rxc = self._process_rx(rxc)
                if rxc:
                    for channel in self._settings["pubchannels"]:
                        self._log.debug(str(rxc.uri) + " Sent to channel(start)' : " + str(channel))
                       
                        # Initialize channel if needed
                        if not channel in self._pub_channels:
                            self._pub_channels[channel] = []
                            
                        # Add cargo item to channel
                        self._pub_channels[channel].append(rxc)
                        
                        self._log.debug(str(rxc.uri) + " Sent to channel(end)' : " + str(channel))

            # Subscriber channels
            for channel in self._settings["subchannels"]:
                if channel in self._sub_channels:
                    for i in range(0,len(self._sub_channels[channel])):
                        frame = self._sub_channels[channel].pop(0)
                        self.add(frame)
                    
            # Don't loop to fast
            time.sleep(0.1)
            # Action reporter tasks
            self.action()
            
    def add(self, cargo):
        """Append data to buffer.

        data (list): node and values (eg: '[node,val1,val2,...]')

        """

        # Create a frame of data in "emonCMS format"
        f = []
        try:
            f.append(cargo.timestamp)
            f.append(cargo.nodeid)
            for i in cargo.realdata:
                f.append(i)
            if cargo.rssi:
                f.append(cargo.rssi)
                
            # self._log.debug(str(cargo.uri) + " adding frame to buffer => "+ str(f))
            
        except:
            self._log.warning("Failed to create emonCMS frame " + str(f))
            
        # self._log.debug(str(carg.ref) + " added to buffer =>"
        #                 + " time: " + str(carg.timestamp)
        #                 + ", node: " + str(carg.node)
        #                 + ", data: " + str(carg.data))

        # databuffer is of format:
        # [[timestamp, nodeid, datavalues][timestamp, nodeid, datavalues]]
        # [[1399980731, 10, 150, 3450 ...]]
        
        # datauffer format can be overwritten by interfacer
        
        self.buffer.storeItem(f)


    def read(self):
        """Read raw data from interface and pass for processing.
        Specific version to be created for each interfacer
        Returns an EmonHubCargo object
        """
        pass


    def send(self, cargo):
        """Send data from interface.
        Specific version to be created for each interfacer
        Accepts an EmonHubCargo object
        """
        pass


    def action(self):
        """

        :return:
        """

        # pause output if 'pause' set to 'all' or 'out'
        if 'pause' in self._settings \
                and str(self._settings['pause']).lower() in ['all', 'out']:
            return

        # If an interval is set, check if that time has passed since last post
        if int(self._settings['interval']) \
                and time.time() - self._interval_timestamp < int(self._settings['interval']):
            return
        else:
            # Then attempt to flush the buffer
            self.flush()

    def flush(self):
        """Send oldest data in buffer, if any."""
        
        # Buffer management
        # If data buffer not empty, send a set of values
        if self.buffer.hasItems():
            max_items = int(self._settings['batchsize'])
            if max_items > self._item_limit:
                max_items = self._item_limit
            elif max_items <= 0:
                return

            databuffer = self.buffer.retrieveItems(max_items)
            retrievedlength = len(databuffer)
            if self._process_post(databuffer):
                # In case of success, delete sample set from buffer
                self.buffer.discardLastRetrievedItems(retrievedlength)
                # log the time of last succesful post
                self._interval_timestamp = time.time()
            else:
                # slow down retry rate in the case where the last attempt failed
                # stops continuous retry attempts filling up the log
                self._interval_timestamp = time.time() 
            

    def _process_post(self, data):
        """
        To be implemented in subclass.

        :return: True if data posted successfully and can be discarded
        """
        pass

    def _send_post(self, post_url, post_body=None):
        """

        :param post_url:
        :param post_body:
        :return: the received reply if request is successful
        """
        """Send data to server.

        data (list): node and values (eg: '[node,val1,val2,...]')
        time (int): timestamp, time when sample was recorded

        return True if data sent correctly

        """

        reply = ""
        request = urllib2.Request(post_url, post_body)
        try:
            response = urllib2.urlopen(request, timeout=60)
        except urllib2.HTTPError as e:
            self._log.warning(self.name + " couldn't send to server, HTTPError: " +
                              str(e.code))
        except urllib2.URLError as e:
            self._log.warning(self.name + " couldn't send to server, URLError: " +
                              str(e.reason))
        except httplib.HTTPException:
            self._log.warning(self.name + " couldn't send to server, HTTPException")
        except Exception:
            import traceback
            self._log.warning(self.name + " couldn't send to server, Exception: " +
                              traceback.format_exc())
        else:
            reply = response.read()
        finally:
            return reply
            
    def _process_rx(self, cargo):
        """Process a frame of data

        f (string): 'NodeID val1 val2 ...'

        This function splits the string into numbers and check its validity.

        'NodeID val1 val2 ...' is the generic data format. If the source uses 
        a different format, override this method.
        
        Return data as a list: [NodeID, val1, val2]

        """

        # Log data
        self._log.debug(str(cargo.uri) + " NEW FRAME : " + str(cargo.rawdata))

        rxc = cargo
        decoded = []
        node = str(rxc.nodeid)
        datacode = True

        # Discard if data is non-existent
        if len(rxc.realdata) < 1:
            self._log.warning(str(cargo.uri) + " Discarded RX frame 'string too short' : " + str(rxc.realdata))
            return False

        # Discard if anything non-numerical found
        try:
            [float(val) for val in rxc.realdata]
        except Exception:
            self._log.warning(str(cargo.uri) + " Discarded RX frame 'non-numerical content' : " + str(rxc.realdata))
            return False

        # Discard if first value is not a valid node id
        # n = float(rxc.realdata[0])
        # if n % 1 != 0 or n < 0 or n > 31:
        #     self._log.warning(str(cargo.uri) + " Discarded RX frame 'node id outside scope' : " + str(rxc.realdata))
        #     return False

        # check if node is listed and has individual datacodes for each value
        if node in ehc.nodelist and 'rx' in ehc.nodelist[node] and 'datacodes' in ehc.nodelist[node]['rx']:

            # fetch the string of datacodes
            datacodes = ehc.nodelist[node]['rx']['datacodes']

            # fetch a string of data sizes based on the string of datacodes
            datasizes = []
            for code in datacodes:
                datasizes.append(ehc.check_datacode(str(code)))
            # Discard the frame & return 'False' if it doesn't match the summed datasizes
            if len(rxc.realdata) != sum(datasizes):
                self._log.warning(str(rxc.uri) + " RX data length: " + str(len(rxc.realdata)) +
                                  " is not valid for datacodes " + str(datacodes))
                return False
            else:
                # Determine the expected number of values to be decoded
                count = len(datacodes)
                # Set decoder to "Per value" decoding using datacode 'False' as flag
                datacode = False
        else:
            # if node is listed, but has only a single default datacode for all values
            if node in ehc.nodelist and 'rx' in ehc.nodelist[node] and 'datacode' in ehc.nodelist[node]['rx']:
                datacode = ehc.nodelist[node]['rx']['datacode']
            else:
            # when node not listed or has no datacode(s) use the interfacers default if specified
                datacode = self._settings['datacode']
            # Ensure only int 0 is passed not str 0
            if datacode == '0':
                datacode = 0
            # when no (default)datacode(s) specified, pass string values back as numerical values
            if not datacode:
                for val in rxc.realdata:
                    if float(val) % 1 != 0:
                        val = float(val)
                    else:
                        val = int(float(val))
                    decoded.append(val)
            # Discard frame if total size is not an exact multiple of the specified datacode size.
            elif len(rxc.realdata) % ehc.check_datacode(datacode) != 0:
                self._log.warning(str(rxc.uri) + " RX data length: " + str(len(rxc.realdata)) +
                                  " is not valid for datacode " + str(datacode))
                return False
            else:
            # Determine the number of values in the frame of the specified code & size
                count = len(rxc.realdata) / ehc.check_datacode(datacode)

        # Decode the string of data one value at a time into "decoded"
        if not decoded:
            bytepos = int(0)
            for i in range(0, count, 1):
                # Use single datacode unless datacode = False then use datacodes
                dc = str(datacode)
                if not datacode:
                    dc = str(datacodes[i])
                # Determine the number of bytes to use for each value by it's datacode
                size = int(ehc.check_datacode(dc))
                try:
                    value = ehc.decode(dc, [int(v) for v in rxc.realdata[bytepos:bytepos+size]])
                except:
                    self._log.warning(str(rxc.uri) + " Unable to decode as values incorrect for datacode(s)")
                    return False
                bytepos += size
                decoded.append(value)

        # check if node is listed and has individual scales for each value
        if node in ehc.nodelist and 'rx' in ehc.nodelist[node] and 'scales' in ehc.nodelist[node]['rx']:
            scales = ehc.nodelist[node]['rx']['scales']
            # === Removed check for scales length so that failure mode is more gracious ===
            # Discard the frame & return 'False' if it doesn't match the number of scales
            # if len(decoded) != len(scales):
            #     self._log.warning(str(rxc.uri) + " Scales " + str(scales) + " for RX data : " + str(rxc.realdata) + " not suitable " )
            #     return False
            # else:
                  # Determine the expected number of values to be decoded
                  # Set decoder to "Per value" scaling using scale 'False' as flag
            #     scale = False
            if len(scales)>1:
                scale = False
            else:
                scale = "1"
        else:
            # if node is listed, but has only a single default scale for all values
            if node in ehc.nodelist and 'rx' in ehc.nodelist[node] and 'scale' in ehc.nodelist[node]['rx']:
                scale = ehc.nodelist[node]['rx']['scale']
            else:
            # when node not listed or has no scale(s) use the interfacers default if specified
                scale = self._settings['scale']


        if not scale == "1":
            for i in range(0, len(decoded), 1):
                x = scale
                if not scale:
                    if i<len(scales):
                        x = scales[i]
                    else:
                        x = 1

                if x != "1":
                    val = decoded[i] * float(x)
                    if val % 1 == 0:
                        decoded[i] = int(val)
                    else:
                        decoded[i] = float(val)

        rxc.realdata = decoded

        names = []
        if node in ehc.nodelist and 'rx' in ehc.nodelist[node] and 'names' in ehc.nodelist[node]['rx']:
            names = ehc.nodelist[node]['rx']['names']
        rxc.names = names

        nodename = False
        if node in ehc.nodelist and 'nodename' in ehc.nodelist[node]:
            nodename = ehc.nodelist[node]['nodename']
        rxc.nodename = nodename

        if not rxc:
            return False
        self._log.debug(str(rxc.uri) + " Timestamp : " + str(rxc.timestamp))
        self._log.debug(str(rxc.uri) + " From Node : " + str(rxc.nodeid))
        if rxc.target:
            self._log.debug(str(rxc.uri) + " To Target : " + str(rxc.target))
        self._log.debug(str(rxc.uri) + "    Values : " + str(rxc.realdata))
        if rxc.rssi:
            self._log.debug(str(rxc.uri) + "      RSSI : " + str(rxc.rssi))
        
        return rxc


    def _process_tx(self, cargo):
        """Prepare data for outgoing transmission.
        cargo is passed through this chain of processing to scale
        and then break the real values down into byte values,
        Uses the datacode data if available.

        DO NOT OVER-WRITE THE "REAL" VALUE DATA WITH ENCODED DATA !!!
        there may be other threads that need to use cargo.realdata to
        encode data for other targets.

        New "encoded" data is stored as a list of {interfacer:encoded-data} dicts.

        Returns cargo.
        """

        txc = cargo
        scaled = []
        encoded = []

        # Normal operation is dest from txc.nodeid
        if txc.target:
            dest = str(txc.target)
            # self._log.info("dest from txc.target: "+dest)
        else:
            dest = str(txc.nodeid)
            # self._log.info("dest from txc.nodeid: "+dest)

        # self._log.info("Target: "+dest)
        # self._log.info("Realdata: "+json.dumps(txc.realdata))

        # check if node is listed and has individual scales for each value
        if dest in ehc.nodelist and 'tx' in ehc.nodelist[dest] and 'scales' in ehc.nodelist[dest]['tx']:
            scales = ehc.nodelist[dest]['tx']['scales']
            # Discard the frame & return 'False' if it doesn't match the number of scales
            if len(txc.realdata) != len(scales):
                self._log.warning(str(txc.uri) + " Scales " + str(scales) + " for RX data : " + str(txc.realdata) +
                                  " not suitable " )
                return False
            else:
                # Determine the expected number of values to be decoded

                # Set decoder to "Per value" scaling using scale 'False' as flag
                scale = False
        else:
            # if node is listed, but has only a single default scale for all values
            if dest in ehc.nodelist and 'tx' in ehc.nodelist[dest] and 'scale' in ehc.nodelist[dest]['tx']:
                scale = ehc.nodelist[dest]['tx']['scale']
            else:
            # when node not listed or has no scale(s) use the interfacers default if specified
                if 'scale' in self._settings:
                    scale = self._settings['scale']
                else:
                    scale = "1"

        if scale == "1":
            scaled = txc.realdata
        else:
            for i in range(0, len(txc.realdata), 1):
                x = scale
                if not scale:
                    x = scales[i]
                if x == "1":
                    val = txc.realdata[i]
                else:
                    val = float(txc.realdata[i]) / float(x)
                    if val % 1 == 0:
                        val = int(val)
                scaled.append(val)


        # check if node is listed and has individual datacodes for each value
        if (dest in ehc.nodelist and 'tx' in ehc.nodelist[dest] and 'datacodes' in ehc.nodelist[dest]['tx']):

            # fetch the string of datacodes
            datacodes = ehc.nodelist[dest]['tx']['datacodes']

            # fetch a string of data sizes based on the string of datacodes
            datasizes = []
            for code in datacodes:
                datasizes.append(ehc.check_datacode(str(code)))
            # Discard the frame & return 'False' if it doesn't match the summed datasizes
            if len(scaled) != len(datasizes):
                self._log.warning(str(txc.uri) + " TX datacodes: " + str(datacodes) +
                                  " are not valid for values " + str(scaled))
                return False
            else:
                # Determine the expected number of values to be decoded
                count = len(scaled)
                # Set decoder to "Per value" decoding using datacode 'False' as flag
                datacode = False
        else:
            # if node is listed, but has only a single default datacode for all values
            if dest in ehc.nodelist and 'tx' in ehc.nodelist[dest] and 'datacode' in ehc.nodelist[dest]['tx']:
                datacode = ehc.nodelist[dest]['tx']['datacode']
            else:
            # when node not listed or has no datacode(s) use the interfacers default if specified
                if 'datacode' in self._settings:
                    datacode = self._settings['datacode']
                else:
                    datacode = "h"

            # Ensure only int 0 is passed not str 0
            if datacode == '0':
                datacode = 0
            # when no (default)datacode(s) specified, pass string values back as numerical values
            if not datacode:
                encoded.append(dest)
                for val in scaled:
                    if float(val) % 1 != 0:
                        val = float(val)
                    else:
                        val = int(float(val))
                    encoded.append(val)
            # Discard frame if total size is not an exact multiple of the specified datacode size.
            # elif len(data) * ehc.check_datacode(datacode) != 0:
            #     self._log.warning(str(uri) + " TX data length: " + str(len(data)) +
            #                       " is not valid for datacode " + str(datacode))
            #     return False
            else:
            # Determine the number of values in the frame of the specified code & size
                count = len(scaled) #/ ehc.check_datacode(datacode)

        if not encoded:
            encoded.append(dest)
            for i in range(0, count, 1):
                # Use single datacode unless datacode = False then use datacodes
                dc = str(datacode)
                if not datacode:
                    dc = str(datacodes[i])
                for b in ehc.encode(dc,int(scaled[i])):
                    encoded.append(b)

        txc.encoded.update({self.getName():encoded})
        return txc

    def set(self, **kwargs):
        """Set configuration parameters.

        **kwargs (dict): settings to be sent. Example:
        {'setting_1': 'value_1', 'setting_2': 'value_2'}

        pause (string): pause status
            'pause' = all  pause Interfacer fully, nothing read, processed or posted.
            'pause' = in   pauses the input only, no input read performed
            'pause' = out  pauses output only, input is read, processed but not posted to buffer
            'pause' = off  pause is off and Interfacer is fully operational (default)
        
        """
    #def setall(self, **kwargs):

        for key, setting in self._defaults.iteritems():
            if key in kwargs.keys():
                setting = kwargs[key]
            else:
                setting = self._defaults[key]
            if key in self._settings and self._settings[key] == setting:
                continue
            elif key == 'pause' and str(setting).lower() in ['all', 'in', 'out', 'off']:
                pass
            elif key in ['interval', 'batchsize'] and setting.isdigit():
                pass
            elif key == 'nodeoffset' and str(setting).isdigit():
                pass
            elif key == 'datacode' and str(setting) in ['0', 'b', 'B', 'h', 'H', 'L', 'l', 'f']:
                pass
            elif key == 'scale' and (int(setting == 1) or not (int(setting % 10))):
                pass
            elif key == 'timestamped' and str(setting).lower() in ['true', 'false']:
                if str(setting).lower()=="true": setting = True
                else: setting = False
                pass
            elif key == 'targeted' and str(setting).lower() in ['true', 'false']:
                if str(setting).lower()=="true": setting = True
                else: setting = False
                pass
            elif key == 'pubchannels':
                pass
            elif key == 'subchannels':
                pass
            else:
                self._log.warning("In interfacer set '%s' is not a valid setting for %s: %s" % (str(setting), self.name, key))
                continue
            self._settings[key] = setting
            self._log.debug("Setting " + self.name + " " + key + ": " + str(setting))

"""class EmonhubSerialInterfacer

Monitors the serial port for data

"""


class EmonHubSerialInterfacer(EmonHubInterfacer):

    def __init__(self, name, com_port='', com_baud=9600):
        """Initialize interfacer

        com_port (string): path to COM port

        """

        # Initialization
        super(EmonHubSerialInterfacer, self).__init__(name)

        # Open serial port
        self._ser = self._open_serial_port(com_port, com_baud)
        
        # Initialize RX buffer
        self._rx_buf = ''

    def close(self):
        """Close serial port"""
        
        # Close serial port
        if self._ser is not None:
            self._log.debug("Closing serial port")
            self._ser.close()

    def _open_serial_port(self, com_port, com_baud):
        """Open serial port

        com_port (string): path to COM port

        """

        #if not int(com_baud) in [75, 110, 300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]:
        #    self._log.debug("Invalid 'com_baud': " + str(com_baud) + " | Default of 9600 used")
        #    com_baud = 9600

        try:
            s = serial.Serial(com_port, com_baud, timeout=0)
            self._log.debug("Opening serial port: " + str(com_port) + " @ "+ str(com_baud) + " bits/s")
        except serial.SerialException as e:
            self._log.error(e)
            raise EmonHubInterfacerInitError('Could not open COM port %s' %
                                           com_port)
        else:
            return s

    def read(self):
        """Read data from serial port and process if complete line received.

        Return data as a list: [NodeID, val1, val2]
        
        """

        # Read serial RX
        self._rx_buf = self._rx_buf + self._ser.readline()
        
        # If line incomplete, exit
        if '\r\n' not in self._rx_buf:
            return

        # Remove CR,LF
        f = self._rx_buf[:-2]

        # Reset buffer
        self._rx_buf = ''

        # Create a Payload object
        c = new_cargo(rawdata=f)

        f = f.split()

        if int(self._settings['nodeoffset']):
            c.nodeid = int(self._settings['nodeoffset'])
            c.realdata = f
        else:
            c.nodeid = int(f[0])
            c.realdata = f[1:]

        return c

"""class EmonHubJeeInterfacer

Monitors the serial port for data from "Jee" type device

"""


class EmonHubJeeInterfacer(EmonHubSerialInterfacer):

    def __init__(self, name, com_port='/dev/ttyAMA0', com_baud=0):
        """Initialize Interfacer

        com_port (string): path to COM port

        """

        # Initialization
        if com_baud != 0:
            super(EmonHubJeeInterfacer, self).__init__(name, com_port, com_baud)
        else:
            super(EmonHubJeeInterfacer, self).__init__(name, com_port, 38400)
        
        # Display device firmware version and current settings
        self.info = ["",""]
        if self._ser is not None:
            self._ser.write("v")
            time.sleep(2)
            self._rx_buf = self._rx_buf + self._ser.readline()
            if '\r\n' in self._rx_buf:
                self._rx_buf=""
                info = self._rx_buf + self._ser.readline()[:-2]
                if info != "":
                    # Split the returned "info" string into firmware version & current settings
                    self.info[0] = info.strip().split(' ')[0]
                    self.info[1] = info.replace(str(self.info[0]), "")
                    self._log.info( self.name + " device firmware version: " + self.info[0])
                    self._log.info( self.name + " device current settings: " + str(self.info[1]))
                else:
                    # since "v" command only v11> recommend firmware update ?
                    #self._log.info( self.name + " device firmware is pre-version RFM12demo.11")
                    self._log.info( self.name + " device firmware version & configuration: not available")
            else:
                self._log.warning("Device communication error - check settings")
        self._rx_buf=""
        self._ser.flushInput()

        # Initialize settings
        self._defaults.update({'pause': 'off', 'interval': 0, 'datacode': 'h'})

        # This line will stop the default values printing to logfile at start-up
        # unless they have been overwritten by emonhub.conf entries
        # comment out if diagnosing a startup value issue
        self._settings.update(self._defaults)

        # Jee specific settings to be picked up as changes not defaults to initialise "Jee" device
        self._jee_settings =  ({'baseid': '15', 'frequency': '433', 'group': '210', 'quiet': 'True', 'calibration': '230V'})
        self._jee_prefix = ({'baseid': 'i', 'frequency': '', 'group': 'g', 'quiet': 'q', 'calibration': 'p'})

        # Pre-load Jee settings only if info string available for checks
        if all(i in self.info[1] for i in (" i", " g", " @ ", " MHz")):
            self._settings.update(self._jee_settings)

    def read(self):
        """Read data from serial port and process if complete line received.

        Return data as a list: [NodeID, val1, val2]

        """

        # Read serial RX
        self._rx_buf = self._rx_buf + self._ser.readline()

        # If line incomplete, exit
        if '\r\n' not in self._rx_buf:
            return

        # Remove CR,LF.
        f = self._rx_buf[:-2].strip()

        # Reset buffer
        self._rx_buf = ''

        if not f:
            return

        if f[0] == '\x01':
            #self._log.debug("Ignoring frame consisting of SOH character" + str(f))
            return

        if f[0] == '?':
            self._log.debug("Discarding RX frame 'unreliable content'" + str(f))
            return False

        # Discard information messages
        if '>' in f:
            if '->' in f:
                self._log.debug("confirmed sent packet size: " + str(f))
                return
            self._log.debug("acknowledged command: " + str(f))
            return

        # Record current device settings
        if " i" and " g" and " @ " and " MHz" in f:
            self.info[1] = f
            self._log.debug("device settings updated: " + str(self.info[1]))
            return

        # Save raw packet to new cargo object
        c = new_cargo(rawdata=f)

        # Convert single string to list of string values
        f = f.split(' ')

        # Strip leading 'OK' from frame if needed
        if f[0]=='OK':
            f = f[1:]

        # Extract RSSI value if it's available
        if str(f[-1])[0]=='(' and str(f[-1])[-1]==')':
            r = f[-1][1:-1]
            try:
                c.rssi = int(r)
            except ValueError:
                self._log.warning("Packet discarded as the RSSI format is invalid: "+ str(f))
                return
            f = f[:-1]

        try:
            # Extract node id from frame
            c.nodeid = int(f[0]) + int(self._settings['nodeoffset'])
        except ValueError:
            return

        try:
            # Store data as a list of integer values
            c.realdata = [int(i) for i in f[1:]]
        except ValueError:
            return

        return c

    def set(self, **kwargs):
        """Send configuration parameters to the "Jee" type device through COM port

        **kwargs (dict): settings to be modified. Available settings are
        'baseid', 'frequency', 'group'. Example:
        {'baseid': '15', 'frequency': '4', 'group': '210'}
        
        """

        for key, setting in self._jee_settings.iteritems():
            # Decide which setting value to use
            if key in kwargs.keys():
                setting = kwargs[key]
            else:
                setting = self._jee_settings[key]
            # convert bools to ints
            if str.capitalize(str(setting)) in ['True', 'False']:
                setting = int(setting == "True")
            # confirmation string always contains baseid, group anf freq
            if " i" and " g" and " @ " and " MHz" in self.info[1]:
                # If setting confirmed as already set, continue without changing
                if (self._jee_prefix[key] + str(setting)) in self.info[1].split():
                    continue
            elif key in self._settings and self._settings[key] == setting:
                continue
            if key == 'baseid' and int(setting) >=1 and int(setting) <=26:
                command = str(setting) + 'i'
            elif key == 'frequency' and setting in ['433','868','915']:
                command = setting[:1] + 'b'
            elif key == 'group'and int(setting) >=0 and int(setting) <=250:
                command = str(setting) + 'g'
            elif key == 'quiet' and int(setting) >=0 and int(setting) <2:
                command = str(setting) + 'q'
            elif key == 'calibration' and setting == '230V':
                command = '1p'
            elif key == 'calibration' and setting == '110V':
                command = '2p'
                
            else:
                self._log.warning("In interfacer set '%s' is not a valid setting for %s: %s" % (str(setting), self.name, key))
                continue
            self._settings[key] = setting
            self._log.info("Setting " + self.name + " %s: %s" % (key, setting) + " (" + command + ")")
            self._ser.write(command)
            # Wait a sec between two settings
            time.sleep(1)

        # include kwargs from parent
        super(EmonHubJeeInterfacer, self).set(**kwargs)

    def action(self):
        """Actions that need to be done on a regular basis. 
        
        This should be called in main loop by instantiater.
        
        """

        t = time.time()

        # Broadcast time to synchronize emonGLCD
        interval = int(self._settings['interval'])
        if interval:  # A value of 0 means don't do anything
            if (t - self._interval_timestamp) > interval:
                self._interval_timestamp = t
                now = datetime.datetime.now()
                self._log.debug(self.name + " broadcasting time: %02d:%02d" % (now.hour, now.minute))
                self._ser.write("00,%02d,%02d,00,s" % (now.hour, now.minute))

    def send (self, cargo):

        f = cargo
        cmd = "s"

        if self.getName() in f.encoded:
            data = f.encoded[self.getName()]
        else:
            data = f.realdata

        payload = ""
        for value in data:
            if int(value) < 0 or int(value) > 255:
                self._log.warning(self.name + " discarding Tx packet: values out of scope" )
                return
            payload += str(int(value))+","
                
        payload += cmd
        
        self._log.debug(str(f.uri) + " sent TX packet: " + payload)
        self._ser.write(payload)


"""class EmonHubSocketInterfacer

Monitors a socket for data, typically from ethernet link

"""


class EmonHubSocketInterfacer(EmonHubInterfacer):

    def __init__(self, name, port_nb=50011):
        """Initialize Interfacer

        port_nb (string): port number on which to open the socket

        """

        # Initialization
        super(EmonHubSocketInterfacer, self).__init__(name)

        # add an apikey setting
        self._skt_settings = {'apikey':""}
        self._settings.update(self._skt_settings)

        # Open socket
        self._socket = self._open_socket(port_nb)

        # Initialize RX buffer for socket
        self._sock_rx_buf = ''

    def _open_socket(self, port_nb):
        """Open a socket

        port_nb (string): port number on which to open the socket

        """

        self._log.debug('Opening socket on port %s', port_nb)

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('', int(port_nb)))
            s.listen(1)
        except socket.error as e:
            self._log.error(e)
            raise EmonHubInterfacerInitError('Could not open port %s' %
                                           port_nb)
        else:
            return s

    def close(self):
        """Close socket."""
        
        # Close socket
        if self._socket is not None:
            self._log.debug('Closing socket')
            self._socket.close()

    def read(self):
        """Read data from socket and process if complete line received.

        Return data as a list: [NodeID, val1, val2]
        
        """

        # Check if data received
        ready_to_read, ready_to_write, in_error = \
            select.select([self._socket], [], [], 0)

        # If data received, add it to socket RX buffer
        if self._socket in ready_to_read:

            # Accept connection
            conn, addr = self._socket.accept()
            
            # Read data
            self._sock_rx_buf = self._sock_rx_buf + conn.recv(1024)
            
            # Close connection
            conn.close()

        # If there is at least one complete frame in the buffer
        if not '\r\n' in self._sock_rx_buf:
            return

        # Process and return first frame in buffer:
        f, self._sock_rx_buf = self._sock_rx_buf.split('\r\n', 1)

        # create a new cargo
        c = new_cargo(rawdata=f)

        # Split string into values
        f = f.split(' ')

        # If apikey is specified, 32chars and not all x's
        if 'apikey' in self._settings:
            if len(self._settings['apikey']) == 32 and self._settings['apikey'].lower != "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx":
                # Discard if apikey is not in received frame
                if not self._settings['apikey'] in f:
                    self._log.warning(str(c.uri) +" discarded frame: apikey not matched")
                    return
                # Otherwise remove apikey from frame
                f = [ v for v in f if self._settings['apikey'] not in v ]
                c.rawdata = ' '.join(f)
        else:
            pass


        # Extract timestamp value if one is expected or use 0
        timestamp = 0.0
        if self._settings['timestamped']:
            c.timestamp=f[0]
            f = f[1:]
        # Extract source's node id
        c.nodeid = int(f[0]) + int(self._settings['nodeoffset'])
        f=f[1:]
        # Extract the Target id if one is expected
        if self._settings['targeted']:
                #setting = str.capitalize(str(setting))
            c.target = int(f[0])
            f = f[1:]
        # Extract list of data values
        c.realdata = f#[1:]
        # Create a Payload object
        #f = new_cargo(data, node, timestamp, dest)

        return c


    def set(self, **kwargs):
        """

        """

        for key, setting in self._skt_settings.iteritems():
            # Decide which setting value to use
            if key in kwargs.keys():
                setting = kwargs[key]
            else:
                setting = self._skt_settings[key]
            if key in self._settings and self._settings[key] == setting:
                continue
            elif key == 'apikey':
                if str.lower(setting[:4]) == 'xxxx':
                    self._log.warning("Setting " + self.name + " apikey: obscured")
                    pass
                elif str.__len__(setting) == 32 :
                    self._log.info("Setting " + self.name + " apikey: set")
                    pass
                elif setting == "":
                    self._log.info("Setting " + self.name + " apikey: null")
                    pass
                else:
                    self._log.warning("Setting " + self.name + " apikey: invalid format")
                    continue
                self._settings[key] = setting
                # Next line will log apikey if uncommented (privacy ?)
                #self._log.debug(self.name + " apikey: " + str(setting))
                continue
            elif key == 'url' and setting[:4] == "http":
                self._log.info("Setting " + self.name + " url: " + setting)
                self._settings[key] = setting
                continue
            else:
                self._log.warning("'%s' is not valid for %s: %s" % (str(setting), self.name, key))

        # include kwargs from parent
        super(EmonHubSocketInterfacer, self).set(**kwargs)





"""class EmonHubPacketGenInterfacer

Monitors a socket for data, typically from ethernet link

"""


class EmonHubPacketGenInterfacer(EmonHubInterfacer):

    def __init__(self, name):
        """Initialize interfacer

        """

        # Initialization
        super(EmonHubPacketGenInterfacer, self).__init__(name)

        self._control_timestamp = 0
        self._control_interval = 5
        self._defaults.update({'interval': 5, 'datacode': 'b'})
        self._pg_settings = {'apikey': "", 'url': 'http://localhost/emoncms'}
        self._settings.update(self._pg_settings)

    def read(self):
        """Read data from the PacketGen emonCMS module.

        """
        t = time.time()

        if not (t - self._control_timestamp) > self._control_interval:
            return

        req = self._settings['url'] + \
              "/emoncms/packetgen/getpacket.json?apikey="

        try:
            packet = urllib2.urlopen(req + self._settings['apikey']).read()
        except:
            return

        # logged without apikey added for security
        self._log.info("requesting packet: " + req + "E-M-O-N-C-M-S-A-P-I-K-E-Y")

        try:
            packet = json.loads(packet)
        except ValueError:
            self._log.warning("no packet returned")
            return

        raw = ""
        target = 0
        values = []
        datacodes = []

        for v in packet:
            raw += str(v['value']) + " "
            values.append(int(v['value']))
            # PacketGen datatypes are 0, 1 or 2 for bytes, ints & bools
            # bools are currently read as bytes 0 & 1
            datacodes.append(['B', 'h', 'B'][v['type']])

        c = new_cargo(rawdata=raw)

        # Extract the Target id if one is expected
        if self._settings['targeted']:
                #setting = str.capitalize(str(setting))
            c.target = int(values[0])
            values = values[1:]
            datacodes = datacodes[1:]

        c.realdata = values
        c.realdatacodes = datacodes

        self._control_timestamp = t
        c.timestamp = t

        # Return a Payload object
        #x = new_cargo(realdata=data)
        #x.realdatacodes = datacodes
        return c


    def action(self):
        """Actions that need to be done on a regular basis.

        This should be called in main loop by instantiater.

        """

        t = time.time()

        # Keep in touch with PacketGen and update refresh time
        interval = int(self._settings['interval'])
        if interval:  # A value of 0 means don't do anything
            if not (t - self._interval_timestamp) > interval:
                return

            try:
                 z = urllib2.urlopen(self._settings['url'] +
                                     "/emoncms/packetgen/getinterval.json?apikey="
                                     + self._settings['apikey']).read()
                 i = int(z[1:-1])
            except:
                self._log.info("request interval not returned")
                return

            if self._control_interval != i:
                self._control_interval = i
                self._log.info("request interval set to: " + str(i) + " seconds")

            self._interval_timestamp = t

        return

    def set(self, **kwargs):
        """

        """

        for key, setting in self._pg_settings.iteritems():
            # Decide which setting value to use
            if key in kwargs.keys():
                setting = kwargs[key]
            else:
                setting = self._pg_settings[key]
            if key in self._settings and self._settings[key] == setting:
                continue
            elif key == 'apikey':
                if str.lower(setting[:4]) == 'xxxx':
                    self._log.warning("Setting " + self.name + " apikey: obscured")
                    pass
                elif str.__len__(setting) == 32 :
                    self._log.info("Setting " + self.name + " apikey: set")
                    pass
                elif setting == "":
                    self._log.info("Setting " + self.name + " apikey: null")
                    pass
                else:
                    self._log.warning("Setting " + self.name + " apikey: invalid format")
                    continue
                self._settings[key] = setting
                # Next line will log apikey if uncommented (privacy ?)
                #self._log.debug(self.name + " apikey: " + str(setting))
                continue
            elif key == 'url' and setting[:4] == "http":
                self._log.info("Setting " + self.name + " url: " + setting)
                self._settings[key] = setting
                continue
            else:
                self._log.warning("'%s' is not valid for %s: %s" % (str(setting), self.name, key))

        # include kwargs from parent
        super(EmonHubPacketGenInterfacer, self).set(**kwargs)


"""class EmonHubMqttGenInterfacer


"""


class EmonHubMqttInterfacer(EmonHubInterfacer):

    def __init__(self, name, mqtt_user=" ", mqtt_passwd=" ", mqtt_host="127.0.0.1", mqtt_port=1883):
        """Initialize interfacer

        """

        # Initialization
        super(EmonHubMqttInterfacer, self).__init__(name)

        # set the default setting values for this interfacer
        self._defaults.update({'datacode': '0'})
        self._settings.update(self._defaults)
        
        # Add any MQTT specific settings
        self._mqtt_settings = {
            # emonhub/rx/10/values format - default emoncms nodes module
            'node_format_enable': 1,
            'node_format_basetopic': 'emonhub/',
            
            # nodes/emontx/power1 format
            'nodevar_format_enable': 0,
            'nodevar_format_basetopic': "nodes/"
        }
        self._settings.update(self._mqtt_settings)
        
        self.init_settings.update({
            'mqtt_host':mqtt_host, 
            'mqtt_port':mqtt_port,
            'mqtt_user':mqtt_user,
            'mqtt_passwd':mqtt_passwd
        })

        self._connected = False          
                  
        self._mqttc = mqtt.Client()
        self._mqttc.on_connect = self.on_connect
        self._mqttc.on_disconnect = self.on_disconnect
        self._mqttc.on_message = self.on_message
        self._mqttc.on_subscribe = self.on_subscribe

    def add(self, cargo):
        """Append data to buffer.
        
          format: {"emontx":{"power1":100,"power2":200,"power3":300}}
          
        """
        nodename = str(cargo.nodeid)
        if cargo.nodename: nodename = cargo.nodename
        
        f = {}
        f['node'] = nodename
        f['data'] = {}
                        
        for i in range(0,len(cargo.realdata)):
            name = str(i+1)
            if i<len(cargo.names):
                name = cargo.names[i]
            value = cargo.realdata[i]
            f['data'][name] = value
        
        if cargo.rssi:
            f['data']['rssi'] = cargo.rssi
        
        self.buffer.storeItem(f)
        
        
    def _process_post(self, databuffer):
        if not self._connected:
            self._log.info("Connecting to MQTT Server")
            try:
                self._mqttc.username_pw_set(self.init_settings['mqtt_user'], self.init_settings['mqtt_passwd'])
                self._mqttc.connect(self.init_settings['mqtt_host'], self.init_settings['mqtt_port'], 60)
            except:
                self._log.info("Could not connect...")
                time.sleep(1.0)
            
        else:
            frame = databuffer[0]
            nodename = frame['node']
            
            # ----------------------------------------------------------
            # General MQTT format: emonhub/rx/emonpi/power1 ... 100
            # ----------------------------------------------------------
            if int(self._settings["nodevar_format_enable"])==1:
                
                for inputname,value in frame['data'].iteritems():
                    # Construct topic
                    topic = self._settings["nodevar_format_basetopic"]+nodename+"/"+inputname
                    payload = str(value)
                    
                    self._log.debug("Publishing: "+topic+" "+payload)
                    result =self._mqttc.publish(topic, payload=payload, qos=2, retain=False)
                    
                    if result[0]==4:
                        self._log.info("Publishing error? returned 4")
                        return False
            
            # ----------------------------------------------------------    
            # Emoncms nodes module format: emonhub/rx/10/values ... 100,200,300
            # ----------------------------------------------------------
            if int(self._settings["node_format_enable"])==1:
            
                topic = self._settings["node_format_basetopic"]+"rx/"+nodename+"/values"
                
                values = []
                for inputname,value in frame['data'].iteritems():
                    values.append(value)
                
                payload = ",".join(map(str,values))
                
                self._log.info("Publishing: "+topic+" "+payload)
                result =self._mqttc.publish(topic, payload=payload, qos=2, retain=False)
                
                if result[0]==4:
                    self._log.info("Publishing error? returned 4")
                    return False
                    
        return True

    def action(self):
        """

        :return:
        """
        self._mqttc.loop(0)

        # pause output if 'pause' set to 'all' or 'out'
        if 'pause' in self._settings \
                and str(self._settings['pause']).lower() in ['all', 'out']:
            return

        # If an interval is set, check if that time has passed since last post
        if int(self._settings['interval']) \
                and time.time() - self._interval_timestamp < int(self._settings['interval']):
            return
        else:
            # Then attempt to flush the buffer
            self.flush()
        
    def on_connect(self, client, userdata, flags, rc):
        
        connack_string = {0:'Connection successful',
                          1:'Connection refused - incorrect protocol version',
                          2:'Connection refused - invalid client identifier',
                          3:'Connection refused - server unavailable',
                          4:'Connection refused - bad username or password',
                          5:'Connection refused - not authorised'}

        if rc:
            self._log.warning(connack_string[rc])
        else:
            self._log.info("connection status: "+connack_string[rc])
            self._connected = True
            # Subscribe to MQTT topics
            self._mqttc.subscribe(str(self._settings["node_format_basetopic"])+"tx/#")
            
        self._log.debug("CONACK => Return code: "+str(rc))
        
    def on_disconnect(self, client, userdata, rc):
        if rc != 0:
            self._log.info("Unexpected disconnection")
            self._connected = False
        
    def on_subscribe(self, mqttc, obj, mid, granted_qos):
        self._log.info("on_subscribe")
        
    def on_message(self, client, userdata, msg):
        topic_parts = msg.topic.split("/")
        
        if topic_parts[0] == self._settings["node_format_basetopic"][:-1]:
            if topic_parts[1] == "tx":
                if topic_parts[3] == "values":
                    nodeid = int(topic_parts[2])
                    
                    payload = msg.payload
                    realdata = payload.split(",")
                    self._log.debug("Nodeid: "+str(nodeid)+" values: "+msg.payload)

                    rxc = Cargo.new_cargo(realdata=realdata)
                    rxc.nodeid = nodeid

                    if rxc:
                        # rxc = self._process_tx(rxc)
                        if rxc:
                            for channel in self._settings["pubchannels"]:
                            
                                # Initialize channel if needed
                                if not channel in self._pub_channels:
                                    self._pub_channels[channel] = []
                                    
                                # Add cargo item to channel
                                self._pub_channels[channel].append(rxc)
                                
                                self._log.debug(str(rxc.uri) + " Sent to channel' : " + str(channel))
                                
    def set(self, **kwargs):
        """

        :param kwargs:
        :return:
        """
        
        super (EmonHubMqttInterfacer, self).set(**kwargs)

        for key, setting in self._mqtt_settings.iteritems():
            #valid = False
            if not key in kwargs.keys():
                setting = self._mqtt_settings[key]
            else:
                setting = kwargs[key]
            if key in self._settings and self._settings[key] == setting:
                continue
            elif key == 'node_format_enable':
                self._log.info("Setting " + self.name + " node_format_enable: " + setting)
                self._settings[key] = setting
                continue
            elif key == 'node_format_basetopic':
                self._log.info("Setting " + self.name + " node_format_basetopic: " + setting)
                self._settings[key] = setting
                continue
            elif key == 'nodevar_format_enable':
                self._log.info("Setting " + self.name + " nodevar_format_enable: " + setting)
                self._settings[key] = setting
                continue
            elif key == 'nodevar_format_basetopic':
                self._log.info("Setting " + self.name + " nodevar_format_basetopic: " + setting)
                self._settings[key] = setting
                continue
            else:
                self._log.warning("'%s' is not valid for %s: %s" % (setting, self.name, key))

"""class EmonHubEmoncmsHTTPInterfacer
"""

class EmonHubEmoncmsHTTPInterfacer(EmonHubInterfacer):

    def __init__(self, name):
        # Initialization
        super(EmonHubEmoncmsHTTPInterfacer, self).__init__(name)
        
        # add or alter any default settings for this reporter
        # defaults previously defined in inherited emonhub_interfacer
        # here we are just changing the batchsize from 1 to 100
        # and the interval from 0 to 30
        self._defaults.update({'batchsize': 100,'interval': 30})
        # This line will stop the default values printing to logfile at start-up
        self._settings.update(self._defaults)
        
        # interfacer specific settings
        self._cms_settings = {
            'apikey': "",
            'url': "http://emoncms.org",
            'senddata': 1,
            'sendstatus': 0
        }
        
        # set an absolute upper limit for number of items to process per post
        self._item_limit = 250
                    
    def _process_post(self, databuffer):
        """Send data to server."""

        # databuffer is of format:
        # [[timestamp, nodeid, datavalues][timestamp, nodeid, datavalues]]
        # [[1399980731, 10, 150, 250 ...]]
        
        if not 'apikey' in self._settings.keys() or str.__len__(str(self._settings['apikey'])) != 32 \
                or str.lower(str(self._settings['apikey'])) == 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx':
            return False
            
            
        data_string = json.dumps(databuffer, separators=(',', ':'))
        
        # Prepare URL string of the form
        # http://domain.tld/emoncms/input/bulk.json?apikey=12345
        # &data=[[0,10,82,23],[5,10,82,23],[10,10,82,23]]
        # &sentat=15' (requires emoncms >= 8.0)

        # time that the request was sent at
        sentat = int(time.time())

        # Construct post_url (without apikey)
        post_url = self._settings['url']+'/input/bulk'+'.json?apikey='
        post_body = "data="+data_string+"&sentat="+str(sentat)

        # logged before apikey added for security
        self._log.info("sending: " + post_url + "E-M-O-N-C-M-S-A-P-I-K-E-Y&" + post_body)

        # Add apikey to post_url
        post_url = post_url + self._settings['apikey']

        # The Develop branch of emoncms allows for the sending of the apikey in the post
        # body, this should be moved from the url to the body as soon as this is widely
        # adopted

        reply = self._send_post(post_url, post_body)
        if reply == 'ok':
            self._log.debug("acknowledged receipt with '" + reply + "' from " + self._settings['url'])
            return True
        else:
            self._log.warning("send failure: wanted 'ok' but got '" +reply+ "'")
            return False
            
            
    def sendstatus(self):
        if not 'apikey' in self._settings.keys() or str.__len__(str(self._settings['apikey'])) != 32 \
                or str.lower(str(self._settings['apikey'])) == 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx':
            return
        
        # MYIP url
        post_url = self._settings['url']+'/myip/set.json?apikey='
        # Print info log
        self._log.info("sending: " + post_url + "E-M-O-N-C-M-S-A-P-I-K-E-Y")
        # add apikey
        post_url = post_url + self._settings['apikey']
        # send request
        reply = self._send_post(post_url,None)
            
    def set(self, **kwargs):
        """

        :param kwargs:
        :return:
        """

        super (EmonHubEmoncmsHTTPInterfacer, self).set(**kwargs)

        for key, setting in self._cms_settings.iteritems():
            #valid = False
            if not key in kwargs.keys():
                setting = self._cms_settings[key]
            else:
                setting = kwargs[key]
            if key in self._settings and self._settings[key] == setting:
                continue
            elif key == 'apikey':
                if str.lower(setting[:4]) == 'xxxx':
                    self._log.warning("Setting " + self.name + " apikey: obscured")
                    pass
                elif str.__len__(setting) == 32 :
                    self._log.info("Setting " + self.name + " apikey: set")
                    pass
                elif setting == "":
                    self._log.info("Setting " + self.name + " apikey: null")
                    pass
                else:
                    self._log.warning("Setting " + self.name + " apikey: invalid format")
                    continue
                self._settings[key] = setting
                # Next line will log apikey if uncommented (privacy ?)
                #self._log.debug(self.name + " apikey: " + str(setting))
                continue
            elif key == 'url' and setting[:4] == "http":
                self._log.info("Setting " + self.name + " url: " + setting)
                self._settings[key] = setting
                continue
            elif key == 'senddata':
                self._log.info("Setting " + self.name + " senddata: " + setting)
                self._settings[key] = setting
                continue
            elif key == 'sendstatus':
                self._log.info("Setting " + self.name + " sendstatus: " + setting)
                self._settings[key] = setting
                continue
            else:
                self._log.warning("'%s' is not valid for %s: %s" % (setting, self.name, key))
                
                                
"""class EmonHubInterfacerInitError

Raise this when init fails.

"""


class EmonHubInterfacerInitError(Exception):
    pass




class EmonHubCargo(object):
    uri = 0
    timestamp = 0.0
    target = 0
    nodeid = 0
    nodename = False
    names = []
    realdata = []
    rssi = 0

    # The class "constructor" - It's actually an initializer
    def __init__(self, timestamp, target, nodeid, nodename, names, realdata, rssi, rawdata):
        EmonHubCargo.uri += 1
        self.uri = EmonHubCargo.uri
        self.timestamp = float(timestamp)
        self.target = int(target)
        self.nodeid = int(nodeid)
        self.nodename = nodename
        self.names = names
        self.realdata = realdata
        self.rssi = int(rssi)

        # self.datacodes = []
        # self.datacode = ""
        # self.scale = 0
        # self.scales = []
        self.rawdata = rawdata
        self.encoded = {}
        # self.realdatacodes = []

def new_cargo(rawdata="", nodename=False, names=[], realdata=[], nodeid=0, timestamp=0.0, target=0, rssi=0.0):
    """

    :rtype : object
    """

    if not timestamp:
        timestamp = time.time()
    cargo = EmonHubCargo(timestamp, target, nodeid, nodename, names, realdata, rssi, rawdata)
    return cargo
