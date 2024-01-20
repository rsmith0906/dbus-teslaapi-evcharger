#!/usr/bin/env python

# import normal packages
import platform
import logging
import sys
import os
import sys
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import requests # for http GET
import configparser # for config/ini file

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService
from datetime import datetime

class DbusTeslaAPIService:
  def __init__(self, productname='Tesla API', connection='Tesla API HTTP JSON service'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['Deviceinstance'])
    customname = config['DEFAULT']['CustomName']

    #formatting
    _kwh = lambda p, v: (str(round(v, 2)) + 'kWh')
    _state = lambda p, v: (str(v))
    _mode = lambda p, v: (str(v))
    _a = lambda p, v: (str(round(v, 1)) + 'A')
    _w = lambda p, v: (str(round(v, 1)) + 'W')
    _v = lambda p, v: (str(round(v, 1)) + 'V')

    self._dbusserviceev = VeDbusService("{}.http_{:02d}".format('com.victronenergy.evcharger', deviceinstance))
    self._dbusservicegrid = VeDbusService("{}.http_{:02d}".format('com.victronenergy.grid', deviceinstance))

    logging.debug("%s /DeviceInstance = %d" % ('com.victronenergy.evcharger', deviceinstance))
    logging.debug("%s /DeviceInstance = %d" % ('com.victronenergy.grid', deviceinstance))

    self._runningSeconds = 0
    self._startDate = datetime.now()
    self._lastCheck = datetime(2023, 12, 8)
    self._running = False
    self._carData = {}

    self.add_standard_paths(self._dbusserviceev, productname, customname, connection, deviceinstance, config, {
          '/Mode': {'initial': 0, 'textformat': _mode},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/Power': {'initial': 0, 'textformat': _w},
          '/Status': {'initial': 0, 'textformat': _state},
          '/SetCurrent': {'initial': 0, 'textformat': _a},
          '/MaxCurrent': {'initial': 0, 'textformat': _a},
          '/Current': {'initial': 0, 'textformat': _a},
          '/ChargingTime': {'initial': 0, 'textformat': _a},
          '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh},
        })

    self.add_standard_paths(self._dbusservicegrid, "Grid", "Grid", connection, deviceinstance, config, {
          '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
          '/Ac/Energy/Power': {'initial': 0, 'textformat': _w},
          '/Ac/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L2/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L2/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L2/Voltage': {'initial': 0, 'textformat': _v},
        })

    # last update
    self._lastUpdate = 0

    # add _update function 'timer'
    gobject.timeout_add(500, self._update) # pause 250ms before the next request

    # add _signOfLife 'timer' to get feedback in log every 5minutes
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)

  def add_standard_paths(self, dbusservice, productname, customname, connection, deviceinstance, config, paths):
      # Create the management objects, as specified in the ccgx dbus-api document
      dbusservice.add_path('/Mgmt/ProcessName', __file__)
      dbusservice.add_path('/Mgmt/ProcessVersion', 'Unknown version, and running on Python ' + platform.python_version())
      dbusservice.add_path('/Mgmt/Connection', connection)

      # Create the mandatory objects
      dbusservice.add_path('/DeviceInstance', deviceinstance)
      dbusservice.add_path('/ProductId', 0xFFFF) # id assigned by Victron Support from SDM630v2.py
      dbusservice.add_path('/ProductName', productname)
      dbusservice.add_path('/CustomName', customname)
      dbusservice.add_path('/Connected', 1)
      dbusservice.add_path('/Latency', None)
      dbusservice.add_path('/FirmwareVersion', self._getTeslaAPIVersion())
      dbusservice.add_path('/HardwareVersion', 0)
      dbusservice.add_path('/Position', int(config['DEFAULT']['Position']))
      dbusservice.add_path('/Serial', self._getTeslaAPISerial())
      dbusservice.add_path('/UpdateIndex', 0)

      # add path values to dbus
      for path, settings in paths.items():
        dbusservice.add_path(
          path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config;

  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT']['SignOfLifeLog']

    if not value:
        value = 0

    return int(value)

  def _getTeslaAPISerial(self):
    car_data = self._getTeslaAPIData()
    vin = car_data['response']['vin']
    if not vin:
        vin = 0
    return str(vin)

  def _getTeslaAPIVersion(self):
    car_data = self._getTeslaAPIData()
    version = car_data['response']['vehicle_state']['car_version']
    if not version:
        version = 0
    return str(version)

  def _getTeslaAPIStatusUrl(self):
    config = self._getConfig()
    URL = "https://owner-api.teslamotors.com/api/1/vehicles/%s/vehicle_data" % (config['DEFAULT']['VehicleId'])
    return URL


  def _getTeslaAPIData(self):
    config = self._getConfig()
    URL = self._getTeslaAPIStatusUrl()
    token = config['DEFAULT']['Token']

    headers = {
        'Authorization': f'Bearer {token}'
    }

    checkDiff = datetime.now() - self._lastCheck
    checkSecs = checkDiff.total_seconds()

    if checkSecs > 10:
       response = requests.get(url = URL, headers=headers)
       # check for response
       if not response:
          raise ConnectionError("No response from TeslaAPI - %s" % (URL))
       self._carData = response.json()
       self._lastCheck = datetime.now()

    # check for Json
    if not self._carData:
        raise ValueError("Converting response to JSON failed")
    
    return self._carData


  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last _update() call: %s" % (self._lastUpdate))
    logging.info("Last '/Ac/Out/L1/V': %s" % (self._dbusserviceev['/Ac/Out/L1/V']))
    logging.info("--- End: sign of life ---")
    return True

  def _update(self):
    try:
       #get data from TeslaAPI Plug
       car_data = self._getTeslaAPIData()

       config = self._getConfig()
       str(config['DEFAULT']['Phase'])

       inverter_phase = str(config['DEFAULT']['Phase'])

       #send data to DBus
       for phase in ['L1']:
         pre = '/Ac/' + phase

         if phase == inverter_phase:
           current = car_data['response']['charge_state']['charge_amps']
           voltage = car_data['response']['charge_state']['charger_voltage']
           charge_state = car_data['response']['charge_state']['charging_state']
           charge_port_latch = car_data['response']['charge_state']['charge_port_latch']
           charge_energy_added = car_data['response']['charge_state']['charge_energy_added']

           power = voltage * current

           self._dbusserviceev['/Current'] = current
           self._dbusserviceev['/Ac/Power'] = power
           self._dbusserviceev[pre + '/Power'] = power
           self._dbusserviceev['/Ac/Energy/Forward'] = charge_energy_added

           charging = False

           if charge_state == 'Stopped':
              if charge_port_latch == 'Engaged':
                 self._dbusserviceev['/Status'] = 1
              else:
                 self._dbusserviceev['/Status'] = 0
                 self._dbusserviceev['/ChargingTime'] = 0
           else:
              self._dbusserviceev['/Status'] = 2
              charging = True

           if power > 0:
             if voltage > 120:
                self._dbusservicegrid['/Ac/Power'] = power
                self._dbusservicegrid['/Ac/L1/Voltage'] = 120
                self._dbusservicegrid['/Ac/L1/Current'] = int(current) / 2
                self._dbusservicegrid['/Ac/L1/Power'] = int(power) / 2
                self._dbusservicegrid['/Ac/L2/Voltage'] = 120
                self._dbusservicegrid['/Ac/L2/Current'] = int(current) / 2
                self._dbusservicegrid['/Ac/L2/Power'] = int(power) / 2
             else:
                self._dbusservicegrid['/Ac/L1/Voltage'] = 120
                self._dbusservicegrid['/Ac/L2/Voltage'] = 0

             if not self._running:
                self._startDate = datetime.now()
                self._running = True

             if charging:
                delta = datetime.now() - self._startDate
                self._dbusserviceev['/ChargingTime'] = delta.total_seconds()
           else:
             self._startDate = datetime.now()
             self._dbusserviceev['/ChargingTime'] = 0
             self._running = False

         else:
           self._dbusserviceev['/Ac/Power'] = 0
           self._dbusserviceev[pre + '/Power'] = 0
           self._dbusserviceev['/Status'] = 0

       self._dbusserviceev['/Ac/L1/Power'] = self._dbusserviceev['/Ac/' + inverter_phase + '/Power']

       #logging
       logging.debug("Inverter Consumption (/Ac/L1/Power): %s" % (self._dbusserviceev['/Ac/L1/Power']))
       logging.debug("---");

       # increment UpdateIndex - to show that new data is available
       index = self._dbusserviceev['/UpdateIndex'] + 1  # increment index
       if index > 255:   # maximum value of the index
         index = 0       # overflow from 255 to 0
       self._dbusserviceev['/UpdateIndex'] = index

       #update lastupdate vars
       self._lastUpdate = time.time()
    except Exception as e:
       self._dbusserviceev['/Status'] = 10
       logging.critical('Error at %s', '_update', exc_info=e)

    # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
    return True

  def _handlechangedvalue(self, path, value):
    logging.debug("someone else updated %s to %s" % (path, value))
    return True # accept the change



def main():
  #configure logging
  logging.basicConfig(      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=logging.INFO,
                            handlers=[
                                logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                                logging.StreamHandler()
                            ])

  try:
      logging.info("Start");

      from dbus.mainloop.glib import DBusGMainLoop
      # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
      DBusGMainLoop(set_as_default=True)

      #formatting
      _kwh = lambda p, v: (str(round(v, 2)) + 'kWh')
      _state = lambda p, v: (str(v))
      _mode = lambda p, v: (str(v))
      _a = lambda p, v: (str(round(v, 1)) + 'A')
      _w = lambda p, v: (str(round(v, 1)) + 'W')
      _v = lambda p, v: (str(round(v, 1)) + 'V')

      #start our main-service
      pvac_output = DbusTeslaAPIService()

      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
if __name__ == "__main__":
  main()
