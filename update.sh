#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

rm $SCRIPT_DIR/dbus-teslaapi-evcharger.py
wget https://raw.githubusercontent.com/rsmith0906/dbus-teslaapi-evcharger/main/dbus-teslaapi-evcharger.py
rm $SCRIPT_DIR/current.log
kill $(pgrep -f "python $SCRIPT_DIR/dbus-teslaapi-evcharger.py")
