import os
import json
import requests
import subprocess
import time
import sys
import logging
from pushbullet import Pushbullet
import configparser # for config/ini file

script_dir = os.path.dirname(os.path.abspath(__file__))

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

class DbusTeslaAPIService:
  pb = None

  def __init__(self, productname='Tesla API', connection='Tesla API HTTP JSON service'):
    config = self._getConfig()
    global pb

    pbApiKey = config['DEFAULT']['PushBulletKey']
    pb = Pushbullet(pbApiKey)

  def run(self):
    try:
        attempt = 0
        max_attempts = 2

        logging.basicConfig(      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S',
                                level=logging.INFO,
                                handlers=[
                                    logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                                    logging.StreamHandler()
                                ])

        while attempt < max_attempts:
            try:
                if self.get_token_is_expired():
                    self.get_new_token()

                # Replace subprocess.call with subprocess.check_call to ensure an error is raised if the command fails
                result = subprocess.run(['tesla-control', 'wake'], check=True, stderr=subprocess.PIPE)
                time.sleep(10)
                result = subprocess.run(['tesla-control', 'charging-stop'], check=True, stderr=subprocess.PIPE)

                push = pb.push_note(f"Tesla Charging Stopped", f"Charging has been stopped.")

                break  # Exit loop if successful
            except subprocess.CalledProcessError as e:
                # Check if the error output contains 'token'
                logging.critical('Error at %s', 'main', exc_info=e)
                push = pb.push_note("Tesla Charging Rate Error", e)

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
    except Exception as e:
        logging.critical('Error at %s', 'main', exc_info=e)

  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config;

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

def main():
    #configure logging
    logging.basicConfig(      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                                datefmt='%Y-%m-%d %H:%M:%S',
                                level=logging.INFO,
                                handlers=[
                                    logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                                    logging.StreamHandler()
                            ])

    instance = DbusTeslaAPIService()
    instance.run()
if __name__ == "__main__":
  main()