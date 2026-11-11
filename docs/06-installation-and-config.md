# 06 — Installation, configuration, and the file layout on the Pi

## Where everything lives at runtime

```
/data/                                       <-- survives Venus OS firmware updates
├── rc.local                                 <-- re-runs install.sh on each boot
├── tesla/                                   <-- separate dir, holds secrets + go binary
│   ├── config.json                          {"VIN": "...", "CLIENT_ID": "..."}
│   ├── private.pem                          Ed25519 key whose public half is enrolled in the car
│   ├── token.txt                            current OAuth access token (read by tesla-control)
│   ├── authtoken.txt                        full token JSON {access_token, refresh_token, expires_in,...}
│   └── tokenexpire.txt                      "YYYY-MM-DD HH:MM:SS"
├── usr/local/go/bin/go                      Go toolchain installed here (not /usr) so it survives updates
└── dbus-teslaapi-evcharger/                 <-- this repo
    ├── dbus-teslaapi-evcharger.py
    ├── change-tesla-charging-rate.py
    ├── change-tesla-charging-status.py
    ├── config.ini
    ├── current.log                          rotated by Venus OS (daemontools log)
    ├── install.sh / restart.sh / uninstall.sh / update.sh
    ├── service/
    │   └── run                              `python ../dbus-teslaapi-evcharger.py`
    └── TokenRefresh/                        sidecar service
        ├── tesla-api-token-refresh.py
        ├── config.ini
        ├── install.sh / restart.sh / uninstall.sh / update.sh
        └── service/run

/service/                                    <-- daemontools watches this dir; rootfs (volatile)
├── dbus-teslaapi-evcharger -> /data/dbus-teslaapi-evcharger/service
└── TokenRefresh -> /data/dbus-teslaapi-evcharger/TokenRefresh/service

/tmp/                                        tmpfs (cleared on every reboot)
├── {VehicleId}.json                         last vehicle_data response (cache for change-* scripts)
├── {VehicleId}-chargeStartTime.json         "{ \"ChargingStartTime\": \"<unix-ts>\" }"
└── Inverter.json                            written by *some other process*, read by getInverterPower()

/opt/victronenergy/dbus-systemcalc-py/ext/velib_python/    Victron's vedbus shim
```

The split between `/data/tesla/` (secrets + Go) and `/data/dbus-teslaapi-evcharger/` (this repo) keeps secrets out of the source tree. Both survive firmware updates.

## What `install.sh` does

```bash
SERVICE_NAME=$(basename $SCRIPT_DIR)              # "dbus-teslaapi-evcharger"

chmod a+x restart.sh uninstall.sh service/run

ln -s $SCRIPT_DIR/service /service/$SERVICE_NAME  # daemontools picks this up immediately

# add this very script to /data/rc.local so it re-runs after firmware updates
grep -qxF "$SCRIPT_DIR/install.sh" /data/rc.local || echo "$SCRIPT_DIR/install.sh" >> /data/rc.local
```

Three things are happening:
1. **Make the runfiles executable.** daemontools `supervise` won't run a non-executable `service/run`.
2. **Symlink into `/service/`.** Venus OS's daemontools is configured to start watching anything in `/service/` — within seconds the new symlink is detected and `supervise` spawns the run script, which `exec`s Python.
3. **Persistence after firmware updates.** `/service/` is on rootfs and is wiped on every Venus OS update. `/data/rc.local` is preserved and is automatically run on boot — re-adding `install.sh` to it ensures the symlink is recreated post-update.

## `restart.sh`

```bash
kill $(pgrep -f "python $SCRIPT_DIR/dbus-teslaapi-evcharger.py")
```

Just kills the Python process. daemontools' `supervise` will respawn it within a second. There's no sleep loop or readiness check.

## `uninstall.sh`

```bash
rm /service/$SERVICE_NAME                              # remove symlink
kill $(pgrep -f 'supervise dbus-teslaapi-evcharger')   # kill the supervise process itself
chmod a-x service/run                                  # so it can't restart itself
./restart.sh
```

A bit forceful — killing `supervise` is the only way to make daemontools forget the service without a system reboot.

## `update.sh`

```bash
rm dbus-teslaapi-evcharger.py
wget https://raw.githubusercontent.com/rsmith0906/dbus-teslaapi-evcharger/main/dbus-teslaapi-evcharger.py
rm current.log
kill $(pgrep -f "python ...")
```

In-place self-update — fetches the latest single Python file from main, drops the log, kills the process. daemontools picks up the new file on respawn. Note this pulls only the main script; `change-tesla-*.py`, `config.ini` schema, install scripts are **not** updated by this. The TokenRefresh sidecar has its own `update.sh` doing the same trick.

## `config.ini` reference

Located next to the main script.

```ini
[DEFAULT]
AccessType   = OnPremise          ; vestigial — was relevant for the Shelly fork; ignored here
SignOfLifeLog = 460               ; minutes between "I'm alive" log lines (≈ 7.6 h)
Deviceinstance = 41               ; integer suffix on the dbus service name
CustomName    = TESLACHARGER      ; what the GX GUI shows as the device name
VehicleId     = 1492677889280637  ; Tesla's internal vehicle id (NOT the VIN)
Phase         = L1                ; which AC phase to populate
Position      = 0                 ; 0=AC Output, 1=AC Input, 2=AC Input 2
Token         =                   ; unused
RefreshToken  =                   ; unused (the live tokens live in /data/tesla/)

[ONPREMISE]
Host     = 192.168.178.146         ; vestigial — fork artifact; not referenced by the code
Username =
Password =
```

`VehicleId` is the numeric `id` from `GET /api/1/vehicles` — distinct from the VIN. Find it via:

```bash
curl -H "Authorization: Bearer $TOKEN" https://owner-api.teslamotors.com/api/1/vehicles
```

## `config.json` reference

Located at `/data/tesla/config.json` (NOT in this repo). The Python files reference this directly:

```json
{
    "VIN":       "5YJ3...",
    "CLIENT_ID": "<oauth client id from auth.tesla.com>"
}
```

`CLIENT_ID` is the OAuth client ID for the modern Fleet-API token flow — not `ownerapi`. Setting up this client ID requires registering an application at `developer.tesla.com` (or using a community-provided one for non-commercial use). For the legacy ownerapi flow used by `_getAccessToken`, the code hardcodes `client_id=ownerapi` — that's the well-known public Tesla mobile app ID.

## TokenRefresh sidecar

A second daemontools service in `TokenRefresh/`. Its `tesla-api-token-refresh.py` runs:

```python
self._wait_seconds = 60 * 60 * 4   # 4 hours
gobject.timeout_add(10000, self._update)   # tick every 10 s, gated by 4h wait
```

So every 4 hours it calls `get_new_token()` — same Fleet-API refresh as the main driver — and rewrites `authtoken.txt`/`token.txt`/`tokenexpire.txt`. This is independent of any actual driver activity, so the token never goes stale just because the user hasn't charged in a while.

The two services share `/data/tesla/` files. There is no locking — both can write at the same time. In practice they don't, but a simultaneous refresh could in theory corrupt `authtoken.txt`.

## First-time setup checklist

What a fresh deployment looks like (ordered):

1. **Get root SSH on the Pi running Venus OS.** Settings → General → Access Level → Superuser → Set root password → Enable SSH on LAN. Install SSH public key into `~/.ssh/authorized_keys`. ([Venus OS root access docs](https://www.victronenergy.com/live/ccgx:root_access).)
2. **Install Go** under `/data/usr/local/go/` (so it persists firmware updates).
3. **Install `tesla-control`**: `go install github.com/teslamotors/vehicle-command/cmd/tesla-control@latest` and `tesla-keygen` similarly.
4. **Generate the key pair** and write to `/data/tesla/private.pem`. Print the public key.
5. **Enroll the public key in the car** by visiting `https://tesla.com/_ak/<your-domain>` and tapping "Add" in the Tesla mobile app. (Tesla's HTTPS-based enrollment flow.)
6. **Get an OAuth token pair** for the user's Tesla account (Fleet API), drop into `/data/tesla/authtoken.txt`.
7. **Create `/data/tesla/config.json`** with `VIN` and `CLIENT_ID`.
8. **Clone this repo to `/data/dbus-teslaapi-evcharger/`** — and `TokenRefresh` along with it.
9. **Edit `config.ini`** — set `Deviceinstance`, `CustomName`, `VehicleId`, `Phase`.
10. **Run `install.sh`** twice (once for the driver, once for `TokenRefresh/`).
11. **Tail logs**: `tail -f /data/dbus-teslaapi-evcharger/current.log`.

The README's quick-start (`wget … main.zip && unzip && install.sh`) only handles step 8–10 and assumes everything else is already in place.
