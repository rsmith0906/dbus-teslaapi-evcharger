#!/usr/bin/env python3
"""
One-shot Tesla Fleet API token bootstrap.

When the refresh_token in /data/tesla/authtoken.txt is dead (expired,
revoked, or never set), the auto-refresh in change-tesla-charging-rate.py
and the TokenRefresh service can't recover. Run this once to mint a fresh
refresh_token via Tesla's authorization-code flow against your registered
Fleet API app.

Usage on the Pi:
    cd /data/dbus-teslaapi-evcharger
    python3 bootstrap-tesla-token.py

You'll need:
  - /data/tesla/config.json with CLIENT_ID set (already there)
  - Your Fleet API app's CLIENT_SECRET (from developer.tesla.com)
  - The redirect_uri you registered for that app (e.g. https://localhost/callback)
  - A browser on any machine to complete the Tesla login
"""

import json
import os
import sys
import time
import urllib.parse
from getpass import getpass

import requests

CONFIG_PATH = '/data/tesla/config.json'
AUTHTOKEN_PATH = '/data/tesla/authtoken.txt'
TOKEN_PATH = '/data/tesla/token.txt'
TOKEN_EXPIRE_PATH = '/data/tesla/tokenexpire.txt'

AUTH_URL = 'https://auth.tesla.com/oauth2/v3/authorize'
TOKEN_URL = 'https://auth.tesla.com/oauth2/v3/token'
SCOPES = 'openid offline_access user_data vehicle_device_data vehicle_cmds vehicle_charging_cmds'


def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"ERROR: {CONFIG_PATH} not found. Create it first with VIN and CLIENT_ID.")
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    if 'CLIENT_ID' not in cfg or not cfg['CLIENT_ID']:
        sys.exit(f"ERROR: CLIENT_ID missing from {CONFIG_PATH}.")
    return cfg


def prompt_secret_and_redirect(cfg):
    secret = cfg.get('CLIENT_SECRET') or getpass(
        "CLIENT_SECRET (from developer.tesla.com app): ").strip()
    redirect = cfg.get('REDIRECT_URI') or input(
        "REDIRECT_URI registered with the app (e.g. https://localhost/callback): ").strip()
    if not secret or not redirect:
        sys.exit("ERROR: CLIENT_SECRET and REDIRECT_URI are both required.")
    return secret, redirect


def build_authorize_url(client_id, redirect_uri, state):
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPES,
        'state': state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def extract_code(redirect_response):
    redirect_response = redirect_response.strip()
    if redirect_response.startswith('http'):
        parsed = urllib.parse.urlparse(redirect_response)
        params = urllib.parse.parse_qs(parsed.query)
        if 'code' not in params:
            sys.exit(f"ERROR: no 'code' in URL. Got: {parsed.query}")
        return params['code'][0]
    return redirect_response


def exchange_code(code, client_id, client_secret, redirect_uri):
    payload = {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'audience': 'https://fleet-api.prd.na.vn.cloud.tesla.com',
        'redirect_uri': redirect_uri,
    }
    r = requests.post(
        TOKEN_URL,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data=payload,
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"ERROR: token exchange failed ({r.status_code}): {r.text}")
    return r.json()


def write_tokens(token_response):
    if 'refresh_token' not in token_response or 'access_token' not in token_response:
        sys.exit(f"ERROR: response missing tokens: {token_response}")

    os.makedirs(os.path.dirname(AUTHTOKEN_PATH), exist_ok=True)

    with open(AUTHTOKEN_PATH, 'w') as f:
        json.dump(token_response, f, indent=4)
    os.chmod(AUTHTOKEN_PATH, 0o600)

    with open(TOKEN_PATH, 'w') as f:
        f.write(token_response['access_token'])
    os.chmod(TOKEN_PATH, 0o600)

    expires_in = token_response.get('expires_in', 28800)
    expire_at = time.time() + (expires_in - 1000)
    expire_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expire_at))
    with open(TOKEN_EXPIRE_PATH, 'w') as f:
        f.write(expire_str)

    print(f"\n  Wrote {AUTHTOKEN_PATH}")
    print(f"  Wrote {TOKEN_PATH}")
    print(f"  Wrote {TOKEN_EXPIRE_PATH} (access token valid until {expire_str})")


def main():
    cfg = load_config()
    client_id = cfg['CLIENT_ID']
    client_secret, redirect_uri = prompt_secret_and_redirect(cfg)

    state = os.urandom(8).hex()
    auth_url = build_authorize_url(client_id, redirect_uri, state)

    print("\n" + "=" * 70)
    print("STEP 1 — open this URL in any browser and log into your Tesla account:")
    print("=" * 70)
    print(f"\n{auth_url}\n")
    print("After you approve, Tesla redirects to your registered redirect_uri")
    print("with a '?code=...&state=...' query string. The page itself will likely")
    print("fail to load (that's fine) — you only need the URL bar's contents.\n")

    redirect_response = input("STEP 2 — paste the full redirect URL (or just the code value): ").strip()
    code = extract_code(redirect_response)
    print(f"  Got code: {code[:12]}...")

    print("\nSTEP 3 — exchanging code for tokens...")
    tokens = exchange_code(code, client_id, client_secret, redirect_uri)
    write_tokens(tokens)

    print("\nDONE. Verify with:")
    print("  TESLA_TOKEN_FILE=/data/tesla/token.txt \\")
    print("    tesla-control -ble=false -command-protocol-version=2 wake")


if __name__ == '__main__':
    main()
