#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

rm $SCRIPT_DIR/tesla-api-token-refresh.py
wget https://raw.githubusercontent.com/rsmith0906/dbus-teslaapi-evcharger/main/TokenRefresh/tesla-api-token-refresh.py
rm $SCRIPT_DIR/current.log
kill $(pgrep -f "python $SCRIPT_DIR/tesla-api-token-refresh.py")
