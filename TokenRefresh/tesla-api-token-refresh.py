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
import requests # for http GET
import configparser # for config/ini file
from datetime import datetime
from decimal import Decimal

class DbusTeslaAPITokenRefreshService:
  def __init__(self, productname='Tesla API Token Refresh', connection='Tesla API Token Refresh'):
    self.config = self._getConfig()

    self._runningSeconds = 0
    self._startDate = datetime.now()
    self._lastTokenRefresh = datetime(2023, 12, 8)
    self._wait_seconds = 60 * 60 * 4 # 4 Hours
    self._running = False
    self._token = None
    
    self.authtoken_file_path = '/data/tesla/authtoken.txt'
    self.token_file_path = '/data/tesla/token.txt'
    self.token_expire_file_path = '/data/tesla/tokenexpire.txt'

    # add _update function 'timer'
    gobject.timeout_add(10000, self._update) # pause 10 seconds before the next request

    # add _signOfLife 'timer' to get feedback in log every 5minutes
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)

    # Load configurations from config.json
    self.config_file_path = os.path.join('/data/tesla/config.json')
    with open(self.config_file_path, 'r') as config_file:
        self.teslaConfig = json.load(config_file)

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

  def _signOfLife(self):
    logging.info("Start: sign of life - Last _update() call: %s" % (self._lastUpdate))
    return True

  def _update(self):
    try:
       config = self._getConfig()

       checkDiff = datetime.now() - self._lastTokenRefresh
       checkSecs = checkDiff.total_seconds()

       if checkSecs > self._wait_seconds:
          logging.info("Getting new token")
          self.get_new_token()
          self._lastTokenRefresh = datetime.now()

    except Exception as e:
      error_message = str(e)
      logging.critical('Error at %s', '_update', exc_info=e)
      
    self._lastUpdate = time.time()
 
    # return true, otherwise add_timeout will be removed from GObject - see docs http://library.isr.ist.utl.pt/docs/pygtk2reference/gobject-functions.html#function-gobject--timeout-add
    return True

  def _showInfoMessage(self, message):
    if not self._lastMessage == message:
      logging.info(message)
      self._lastMessage = message

  def getCurrentDateAsLong(self):
    now = datetime.now()
    timestamp = now.timestamp()
    timestamp_as_long = int(timestamp)
    return timestamp_as_long
       
  def getDateFromLong(self, long):
    return datetime.fromtimestamp(long)

  def get_new_token(self):
    # Reading the refresh token from a json file
    with open(self.authtoken_file_path, 'r') as file:
        data = json.load(file)
        refresh_token = data['refresh_token']

    # Making a POST request to get a new auth token
    url = 'https://auth.tesla.com/oauth2/v3/token'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'grant_type': 'refresh_token',
        'client_id': self.teslaConfig['CLIENT_ID'],
        'refresh_token': refresh_token,
        'scopes': 'user_data vehicle_device_data vehicle_cmds vehicle_charging_cmds'
    }

    response = requests.post(url, headers=headers, data=data)
    response_data = response.json()

    # Checking if the response contains 'refresh_token' and writing to authtoken.txt
    if 'refresh_token' in response_data:
        with open(self.authtoken_file_path, 'w') as file:
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
        with open(self.token_file_path, 'w') as token_file:
            token_file.write(auth_token)

        with open(self.token_expire_file_path, 'w') as token_expire_file:
            token_expire_file.write(expiration_date)
    else:
        print("Auth token is empty. Token not saved.")

  def get_token_is_expired(self):
    if os.path.exists(self.token_expire_file_path):
        with open(self.token_expire_file_path, 'r') as expire_file:
            expiration_date = expire_file.read()
            expiration_date = time.mktime(time.strptime(expiration_date, '%Y-%m-%d %H:%M:%S'))
            if expiration_date < time.time():
                print("Auth token is expired.")
                return True
    else:
      return True
    return False

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

      #start our main-service
      pvac_output = DbusTeslaAPITokenRefreshService()

      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
if __name__ == "__main__":
  main()