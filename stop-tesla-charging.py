import os
import json
import requests
import subprocess
import time

# Load configurations from config.json
config_file_path = os.path.join(os.getcwd(), 'config.json')
with open(config_file_path, 'r') as config_file:
    config = json.load(config_file)

authtoken_file_path = os.path.join(os.getcwd(), 'authtoken.txt')
token_file_path = os.path.join(os.getcwd(), 'token.txt')

# Setting environment variables
os.environ['PATH'] += ':/usr/local/bin:/usr/bin:/bin:/data/usr/local/go/bin'
os.environ['TESLA_VIN'] = config['VIN']
os.environ['TESLA_KEY_NAME'] = 'Tessy'
os.environ['TESLA_KEY_FILE'] = '/data/tesla/private.pem'
os.environ['TESLA_TOKEN_FILE'] = '/data/tesla/token.txt'

# Adding Go bin to PATH
go_path_output = subprocess.check_output(['/data/usr/local/go/bin/go', 'env', 'GOPATH']).decode().strip()
os.environ['PATH'] += f':{go_path_output}/bin'

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

# Saving the new auth token if it's not empty
if auth_token:
    print("New auth token saved to token.txt.")
    with open(token_file_path, 'w') as token_file:
        token_file.write(auth_token)
else:
    print("Auth token is empty. Token not saved.")

# Simulating the tesla-control command with subprocess (requires tesla-control to be a recognized command)
subprocess.call(['tesla-control', 'wake'])
time.sleep(10)
subprocess.call(['tesla-control', 'charging-stop'])
