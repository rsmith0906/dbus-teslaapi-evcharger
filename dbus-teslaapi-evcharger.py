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
import json
import subprocess
import requests # for http GET
import configparser # for config/ini file

script_dir = '/data/tesla'

# Load configurations from config.json
config_file_path = os.path.join(script_dir, 'config.json')
with open(config_file_path, 'r') as config_file:
    config = json.load(config_file)

authtoken_file_path = os.path.join(script_dir, 'authtoken.txt')
token_file_path = os.path.join(script_dir, 'token.txt')
token_expire_file_path = os.path.join(script_dir, 'tokenexpire.txt')

# Setting environment variables
os.environ['PATH'] += ':/usr/local/bin:/usr/bin:/bin:/data/usr/local/go/bin'
os.environ['TESLA_VIN'] = config['VIN']
os.environ['TESLA_KEY_NAME'] = 'Tessy'
os.environ['TESLA_KEY_FILE'] = '/data/tesla/private.pem'
os.environ['TESLA_TOKEN_FILE'] = '/data/tesla/token.txt'

# Adding Go bin to PATH
go_path_output = subprocess.check_output(['/data/usr/local/go/bin/go', 'env', 'GOPATH']).decode().strip()
os.environ['PATH'] += f':{go_path_output}/bin'

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService
from datetime import datetime
from decimal import Decimal

class DbusTeslaAPIService:
  def __init__(self, productname='Tesla API', connection='Tesla API HTTP JSON service'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['Deviceinstance'])
    customname = config['DEFAULT']['CustomName']

    #formatting
    _kwh = lambda p, v: (str(round(v, 2)) + 'kWh')
    _state = lambda p, v: (str(v))
    _mode = lambda p, v: (str(v))
    _startStop = lambda p, v: (str(v))
    _a = lambda p, v: (str(round(v, 1)) + 'A')
    _w = lambda p, v: (str(round(v, 1)) + 'W')
    _v = lambda p, v: (str(round(v, 1)) + 'V')

    self._dbusserviceev = VeDbusService("{}.http_{:02d}".format('com.victronenergy.evcharger', deviceinstance))

    logging.debug("%s /DeviceInstance = %d" % ('com.victronenergy.evcharger', deviceinstance))

    self._runningSeconds = 0
    self._startDate = datetime.now()
    self._lastCheck = datetime(2023, 12, 8)
    self._lastCheckData = datetime(2023, 12, 8)
    self._running = False
    self._firstRun = False
    self._carData = {}
    self._token = None
    self._request_timeout_string = "Request Timeout"
    self._too_many_requests = "Too Many Requests"
    self._wait_seconds = 30
    self._lastMessage = ""
    self._lastUpdate = 0
    self._cacheInverterPower = Decimal(0.0)
    self._cacheChargingPower = -1

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
          '/StartStop': {'initial': 0, 'textformat': _startStop},
        })

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
      config = self._getConfig()
      car_id = config['DEFAULT']['VehicleId']
      self._carData = self.read_data(car_id)
      vin = 0

      if (self._carData):
        vin = self._carData['response']['vin']

      if self.is_not_blank(vin):
         logging.info(f"return cached vin {vin}")
         return vin
        
      if not vin:
        vin = 0
      return str(vin)
      
  def _getTeslaAPIVersion(self):
      config = self._getConfig()
      car_id = config['DEFAULT']['VehicleId']
      self._carData = self.read_data(car_id)
      version = 0

      if self._carData:
        version = self._carData['response']['vehicle_state']['car_version']

      if self.is_not_blank(version):
         logging.info(f"return cached version {version}")
         return version

      if not version:
          version = 0

      return str(version)

  def _getTeslaAPIStatusUrl(self):
    config = self._getConfig()
    URL = "https://owner-api.teslamotors.com/api/1/vehicles/%s/vehicle_data" % (config['DEFAULT']['VehicleId'])
    return URL

  def _getTeslaAPIData(self):
    config = self._getConfig()
    car_id = config['DEFAULT']['VehicleId']
    URL = self._getTeslaAPIStatusUrl()

    if not self._token:
       self._token = self._getAccessToken()

    if not self._token:
        raise ValueError("Could not retrieve Tesla Token")

    token = self._token

    headers = {
        'Authorization': f'Bearer {token}'
    }

    checkDiff = datetime.now() - self._lastCheckData
    checkSecs = checkDiff.total_seconds()

    if checkSecs > self._wait_seconds:
       self._lastCheckData = datetime.now()
       logging.info(f"Last Get Tesla Data: {self._lastCheckData} - Wait in Seconds: {self._wait_seconds}")

       response = requests.get(url = URL, headers=headers)
       response.raise_for_status()

       # check for response
       if not response:
          raise ConnectionError("No response from TeslaAPI - %s" % (URL))
       self._carData = response.json()
       
       # check for Json
       if not self._carData:
          raise ValueError("Converting response to JSON failed")
       
       self.save_data(car_id, json.dumps(self._carData))
       return self._carData
    else:
       return None

  def _getAccessToken(self):
    config = self._getConfig()
    refreshToken = config['DEFAULT']['RefreshToken']

    self._showInfoMessage('Get Access Token')

    URL = 'https://auth.tesla.com/oauth2/v3/token'

    body = {
      'grant_type': 'refresh_token',
      'client_id': 'ownerapi',
      'refresh_token': refreshToken,
      'scope': 'openid email offline_access'
    }

    json_data = json.dumps(body)
    response = requests.post(url = URL, data=json_data, headers={'Content-Type': 'application/json'})

    # check for response
    if not response:
        raise ConnectionError("No response from Tesla API - %s" % (URL))

    response = response.json()

    # check for Json
    if not response:
        raise ValueError("Converting response to JSON failed")

    accessToken = response["access_token"]
    refreshToken = response["refresh_token"]
    expiresIn = response["expires_in"]
    tokenType = response["token_type"]
    
    return accessToken

  def _signOfLife(self):
    logging.info("Start: sign of life - Last _update() call: %s" % (self._lastUpdate))
    return True

  def _setcurrent(self, path, value):
    try:
      # p = requests.post(url = self.CURRENT_URL, data = {'value': value}, timeout=10)
      test = 0
    except Exception as e:
      log.error('Error writing current to station: %s' % e)
      return False
    return True

  def _startstop(self, path, value):
      attempt = 0
      max_attempts = 2
      success = False

      logging.info("StartIt")

      while attempt < max_attempts:
          try:
              logging.info("LoopIt")

              config = self._getConfig()
              car_id = config['DEFAULT']['VehicleId']

              self._carData = self.read_data(car_id)
              if self._carData:
                 charge_state = self._carData['response']['charge_state']['charging_state']
                 logging.info(charge_state)

                 makeChange = True
                 if charge_state == 'Charging' and value == 1:
                  makeChange = False
                  
                 if charge_state != 'Charging' and value == 0:
                  makeChange = False

                 if makeChange:
                   if self.get_token_is_expired():
                      self.get_new_token()

                   # Replace subprocess.call with subprocess.check_call to ensure an error is raised if the command fails
                   result = subprocess.run(['tesla-control', 'wake'], check=True, stderr=subprocess.PIPE)
                   time.sleep(10)

                   if value == 1:
                     result = subprocess.run(['tesla-control', 'charging-start'], check=True, stderr=subprocess.PIPE)
                   else:
                     result = subprocess.run(['tesla-control', 'charging-stop'], check=True, stderr=subprocess.PIPE)

              success = True
              break
          except subprocess.CalledProcessError as e:
              # Check if the error output contains 'token'
              logging.critical('Error at %s', 'main', exc_info=e)
              
              success = False

              error_output = e.stderr.decode('utf-8')
              
              if 'token' in error_output.lower():
                  print("Token error detected, attempting to refresh token.")
                  self.get_new_token()
                  attempt += 1
                  if attempt >= max_attempts:
                      raise RuntimeError(f"Failed to resolve token issue after multiple attempts: {error_output}") from e
              else:
                  # If error is not related to token, re-raise with original error message
                  raise RuntimeError(f"Subprocess command failed: {error_output}") from e

      return success

  def _update(self):
    try:
       config = self._getConfig()
       str(config['DEFAULT']['Phase'])

       charging = False

       if self.is_time_between_midnight_and_8am() and not self._dbusserviceev['/Ac/Power'] > 0:
          self._wait_seconds = 60 * 10

       inverterPower = self.getInverterPower()

       if not self._firstRun:
          self._dbusserviceev['/Mode'] = 0
          self._dbusserviceev['/Connected'] = 1
          self._firstRun = True

       if inverterPower > 500:
          self._wait_seconds = 30

       if abs(self._cacheInverterPower - inverterPower) >= 1.0:
          self._showInfoMessage(f"Inverter Power Level Changed: {inverterPower}")
          self._wait_seconds = 30
          if abs(self._cacheInverterPower - inverterPower) >= 400.0:
             self._lastCheckData = datetime(2023, 12, 8)
          self._cacheInverterPower = inverterPower

       #get data from TeslaAPI Plug
       self._carData = self._getTeslaAPIData()
       if self._carData:
          inverter_phase = str(config['DEFAULT']['Phase'])

          charging_state = self._carData['response']['charge_state']['charging_state']
          if charging_state == "NoPower":
             raise ValueError("NoPower")

          self._showInfoMessage('Car Awake')

          #send data to DBus
          for phase in ['L1']:
            pre = '/Ac/' + phase

            if phase == inverter_phase:
              current = self._carData['response']['charge_state']['charger_actual_current']
              voltage = self._carData['response']['charge_state']['charger_voltage']
              charger_power = self._carData['response']['charge_state']['charger_power']
              charge_state = self._carData['response']['charge_state']['charging_state']
              charge_port_latch = self._carData['response']['charge_state']['charge_port_latch']
              charge_energy_added = self._carData['response']['charge_state']['charge_energy_added']
              max_current = self._carData['response']['charge_state']['charge_current_request_max']
              battery_state = self._carData['response']['charge_state']['battery_level']

              if max_current <= 12:
                if int(charge_energy_added) == 0:
                  self._startDate = datetime.now()
                  self.resetSavedChargeStart()
                
                #self._dbusserviceev['/Ac/Energy/Forward'] = charge_energy_added
                self._dbusserviceev['/MaxCurrent'] = max_current

                if charge_state == 'Stopped' or charging_state == 'Complete':
                    if charge_port_latch == 'Engaged':
                      self._dbusserviceev['/Status'] = 1
                    else:
                      self._dbusserviceev['/Status'] = 0
                      self._dbusserviceev['/ChargingTime'] = 0
                      self._dbusserviceev['/Position'] = 0
                    self._wait_seconds = 60 * 5
                elif charge_state == 'Charging':
                    power = voltage * current
                    self._dbusserviceev['/Status'] = 2
                    self._dbusserviceev['/Current'] = current
                    self._dbusserviceev['/Ac/Power'] = power
                    self._dbusserviceev[pre + '/Power'] = power
                    # self._dbusserviceev["/Mode"] = str(battery_state) + '%'
                    self._wait_seconds = 30
                    self._running = True

                    if (current > 12):
                      self._dbusserviceev['/Position'] = 1
                    else:
                      self._dbusserviceev['/Position'] = 0

                    delta = datetime.now() - self._startDate
                    self._dbusserviceev['/ChargingTime'] = delta.total_seconds()
                    charging = True
                else:
                    self._dbusserviceev['/Status'] = 10
                    self._wait_seconds = 60 * 5

          if not charging:
              self._dbusserviceev['/Ac/Power'] = 0
              self._dbusserviceev[pre + '/Power'] = 0
              self._dbusserviceev['/Current'] = 0
              self._dbusserviceev['/Position'] = 0
              self._running = False

          carDriving = self._getCarDriving()
          if carDriving:
             self._showInfoMessage('Car Driving')
             self._wait_seconds = 60 * 60
             
       #else:
         #if charging:
           #delta = datetime.now() - self._startDate
           #self._dbusserviceev['/ChargingTime'] = delta.total_seconds()
    except Exception as e:
      error_message = str(e)
      if self._request_timeout_string in error_message:
        self._dbusserviceev['/Status'] = 0
        # self._dbusserviceev['/Mode'] = "Car Sleeping"
        self._wait_seconds = 60 * 5
        self._showInfoMessage('Car Sleeping')
      elif self._too_many_requests in error_message:
        self._dbusserviceev['/Status'] = 0
        # self._dbusserviceev['/Mode'] = "Too Many Requests"
        self._wait_seconds = self._wait_seconds + 30
        self._showInfoMessage('Too Many Requests')
      elif "NoPower" in error_message:
        self._dbusserviceev['/Status'] = 0
        # self._dbusserviceev['/Mode'] = "No Power to Charger"
        self._wait_seconds = 60 * 5
        self._showInfoMessage('No Power to Charger')
      else:
        self._wait_seconds = 60 * 5
        self._dbusserviceev['/Status'] = 10
        self._token = self._getAccessToken()
        # self._dbusserviceev['/Mode'] = "Check Logs for Error"
        logging.critical('Error at %s', '_update', exc_info=e)
      
    self._lastUpdate = time.time()
    self._signalChanges()

    # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
    return True

  def _signalChanges(self):
    # increment UpdateIndex - to show that new data is available
    index = self._dbusserviceev['/UpdateIndex'] + 1  # increment index
    if index > 255:   # maximum value of the index
      index = 0       # overflow from 255 to 0
    self._dbusserviceev['/UpdateIndex'] = index

  def _showInfoMessage(self, message):
    if not self._lastMessage == message:
      logging.info(message)
      self._lastMessage = message

  def _getCarDriving(self):
    try:
      carSpeed = self._carData['response']['drive_state']['speed']
      shift_state = self._carData['response']['drive_state']['shift_state']
      if carSpeed or shift_state:
        return True
      else:
        return False
    except Exception as e:
       return False

  def _handlechangedvalue(self, path, value):
    logging.info("someone else updated %s to %s" % (path, value))

    if path == '/StartStop':
      self._startstop(path, value)

    return True # accept the change

  def is_not_blank(self, s):
      return bool(s and not s.isspace())
  
  def save_data(self, key, value):
    file = open(f"/tmp/{key}.json", 'w') 
    file.write(value) 
    file.close() 

  def read_data(self, key):
    try:
      if os.path.exists(f"/tmp/{key}.json"):
        with open(f"/tmp/{key}.json", 'r') as file:
          return json.load(file)
      else:
        return None
    except Exception as e:
      logging.critical('Error at %s', '_update', exc_info=e)
      return None
  
  def get_new_token(self):
    # Reading the refresh token from a json file
    with open(authtoken_file_path, 'r') as file:
        data = json.load(file)
        refresh_token = data['refresh_token']

    # Making a POST request to get a new auth token
    url = 'https://auth.tesla.com/oauth2/v3/token'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'grant_type': 'refresh_token',
        'client_id': config['CLIENT_ID'],
        'refresh_token': refresh_token,
        'scopes': 'user_data vehicle_device_data vehicle_cmds vehicle_charging_cmds'
    }

    response = requests.post(url, headers=headers, data=data)
    response_data = response.json()

    # Checking if the response contains 'refresh_token' and writing to authtoken.txt
    if 'refresh_token' in response_data:
        with open(authtoken_file_path, 'w') as file:
            json.dump(response_data, file, indent=4)
        print("New auth and refresh tokens saved to authtoken.txt.")
    else:
        print("Response does not contain a refresh token.")

    # Extracting the access token, if available
    auth_token = response_data.get('access_token', '')
    expires_in = response_data.get('expires_in', 0)

    expiration_date = time.time() + (expires_in - 1000)
    expiration_date = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiration_date))

    # Saving the new auth token if it's not empty
    if auth_token:
        print("New auth token saved to token.txt.")
        with open(token_file_path, 'w') as token_file:
            token_file.write(auth_token)

        with open(token_expire_file_path, 'w') as token_expire_file:
            token_expire_file.write(expiration_date)
    else:
        print("Auth token is empty. Token not saved.")

  def get_token_is_expired(self):
    if os.path.exists(token_expire_file_path):
        with open(token_expire_file_path, 'r') as expire_file:
            expiration_date = expire_file.read()
            expiration_date = time.mktime(time.strptime(expiration_date, '%Y-%m-%d %H:%M:%S'))
            if expiration_date < time.time():
                print("Auth token is expired.")
                return True
    else:
      return True
    return False

  def getInverterPower(self):
    inverter_data = self.read_data("Inverter")
    if inverter_data:
       return Decimal(inverter_data['Power'])
    else:
       return Decimal(0.0)

  def getCurrentDateAsLong(self):
    now = datetime.now()
    timestamp = now.timestamp()
    timestamp_as_long = int(timestamp)
    return timestamp_as_long
  
  def resetSavedChargeStart(self):
      config = self._getConfig()
      car_id = config['DEFAULT']['VehicleId']
      self.save_data(f"{car_id}-chargeStartTime", f"{{ \"ChargingStartTime\": \"{self.getCurrentDateAsLong()}\" }}")

  def getSavedChargeStart(self):
    try:
      config = self._getConfig()
      car_id = config['DEFAULT']['VehicleId']
      charge_data = self.read_data(f"{car_id}-chargeStartTime")
      if charge_data:
        return datetime.now()
      else:
        return datetime.now()
    except Exception as e:
      return datetime.now()
       
  def getDateFromLong(self, long):
    return datetime.fromtimestamp(long)

  def is_time_between_midnight_and_8am(self):
      # Get the current time
      current_time = datetime.now().time()

      # Define the time for midnight and 8 AM
      midnight = current_time.replace(hour=6, minute=0, second=0, microsecond=0)
      eight_am = current_time.replace(hour=14, minute=0, second=0, microsecond=0)

      # Check if the current time is between midnight and 8 AM
      return midnight <= current_time < eight_am

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

      #start our main-service
      pvac_output = DbusTeslaAPIService()

      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
if __name__ == "__main__":
  main()