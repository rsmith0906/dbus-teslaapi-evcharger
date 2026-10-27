# 10 — Deploying to the Raspberry Pi (Venus OS)

How to get code from this Windows checkout onto the Pi running Victron Venus OS, restart it, and confirm it works. For first-time setup of the Tesla side (keys, Go, `tesla-control`, OAuth app) see [06-installation-and-config.md](06-installation-and-config.md#first-time-setup-checklist). This doc assumes that is already done.

## The target

| Thing | Value |
|---|---|
| Host | `root@192.168.1.105` (hostname `raspberrypi4`) |
| Repo on the Pi | `/data/dbus-teslaapi-evcharger` |
| Secrets / tokens | `/data/tesla/` (`config.json`, `private.pem`, `token.txt`, `authtoken.txt`, `tokenexpire.txt`) |
| Main service | `/service/dbus-teslaapi-evcharger` → symlink to `/data/dbus-teslaapi-evcharger/service` |
| Token sidecar | `/service/TokenRefresh` → symlink to `/data/dbus-teslaapi-evcharger/TokenRefresh/service` |
| Supervisor | daemontools (`svc`, `svstat`), not systemd |
| Logs | `/data/dbus-teslaapi-evcharger/current.log`, `/data/dbus-teslaapi-evcharger/TokenRefresh/current.log` |

Only `/data` survives a Venus OS firmware update. `/service` is wiped; `install.sh` adds itself to `/data/rc.local` so the symlink is recreated on boot (`install.sh:18-28`).

## Gotchas (read first)

1. **Line endings.** This checkout has `core.autocrlf=true`, so shell scripts are CRLF on Windows. Busybox `sh` on the Pi chokes on CRLF in `service/run` and the service never starts. Python tolerates CRLF; shell scripts do not. Always convert `.sh` files and every `service/run` to LF on the Pi after copying (step 3 below).
2. **`service/run` must `exec python`.** Without `exec`, daemontools signals the `sh` wrapper, the Python process is orphaned and keeps holding the DBus name, and the restarted copy crash-loops on `NameExistsException`. The main service does this correctly (`service/run:8`). `TokenRefresh/service/run:5` does **not** use `exec` yet. It has no DBus name to collide on, but a restart can still leave an orphan running. Fix or check for it with `ps | grep token-refresh`.
3. **Do not use `update.sh` for this branch.** It downloads `dbus-teslaapi-evcharger.py` from GitHub `main` (`update.sh:5`), which overwrites whatever branch you deployed with old code. Copy files with `scp` instead.
4. **Do not overwrite `config.ini` blindly.** The Pi's copy holds the real `VehicleId`, `Deviceinstance`, and optional keys like `BatteryDeviceInstance` (`dbus-teslaapi-evcharger.py:107`) and `PushBulletKey`. Diff before copying.
5. **Never copy `/data/tesla/` contents into the repo.** Tokens and the private key live only on the Pi.
6. **Polling limits.** The driver polls Tesla every 120 s while charging and 300 s when idle (`dbus-teslaapi-evcharger.py:83-84`). Do not shorten these. At 30 s Tesla disables the Fleet API account with `403 EXCEEDED_LIMIT`.

## Deploy steps

Run from PowerShell or Git Bash in the repo root on Windows.

### 1. Check SSH works

```bash
ssh root@192.168.1.105 "uname -a; svstat /service/dbus-teslaapi-evcharger /service/TokenRefresh"
```

### 2. Back up the current files on the Pi

```bash
ssh root@192.168.1.105 "cd /data && tar czf /data/dbus-teslaapi-evcharger-backup-$(date +%Y%m%d-%H%M).tgz dbus-teslaapi-evcharger"
```

### 3. Copy changed files

Copy only what changed. Typical driver change:

```bash
scp dbus-teslaapi-evcharger.py root@192.168.1.105:/data/dbus-teslaapi-evcharger/
```

Other files, as needed:

```bash
scp change-tesla-charging-rate.py change-tesla-charging-status.py bootstrap-tesla-token.py root@192.168.1.105:/data/dbus-teslaapi-evcharger/
scp service/run root@192.168.1.105:/data/dbus-teslaapi-evcharger/service/run
scp TokenRefresh/tesla-api-token-refresh.py root@192.168.1.105:/data/dbus-teslaapi-evcharger/TokenRefresh/
```

Then strip CRLF and restore execute bits on the Pi:

```bash
ssh root@192.168.1.105 'cd /data/dbus-teslaapi-evcharger && \
  sed -i "s/\r$//" *.sh *.py service/run TokenRefresh/*.sh TokenRefresh/*.py TokenRefresh/service/run && \
  chmod 755 service/run TokenRefresh/service/run && chmod 744 *.sh TokenRefresh/*.sh'
```

### 4. Restart the service

```bash
ssh root@192.168.1.105 "svc -t /service/dbus-teslaapi-evcharger"
```

`svc -t` sends SIGTERM; daemontools restarts it within about a second. For the sidecar, use `svc -t /service/TokenRefresh`. `restart.sh` does the same job with `kill` (`restart.sh:4`).

### 5. Verify

```bash
ssh root@192.168.1.105 "svstat /service/dbus-teslaapi-evcharger; tail -n 50 /data/dbus-teslaapi-evcharger/current.log"
```

Good signs:
- `svstat` shows `up (pid …) N seconds` and N keeps growing on repeat runs. If N stays at 0–1, it is crash-looping. Read the log.
- The log shows `Car Awake` or a normal sleep/idle message, not a traceback.
- `dbus -y com.victronenergy.evcharger.http_41 /Status GetValue` returns a number (the `41` is `Deviceinstance` in `config.ini`).
- The EVCS tile appears in the GX Remote Console and on VRM.

Live tail while testing: `ssh root@192.168.1.105 "tail -f /data/dbus-teslaapi-evcharger/current.log"`.

## Rolling back

```bash
ssh root@192.168.1.105 "cd /data && tar xzf dbus-teslaapi-evcharger-backup-<stamp>.tgz && svc -t /service/dbus-teslaapi-evcharger"
```

## Fresh install on a new Pi

After the Tesla prerequisites in doc 06 are in place:

```bash
scp -r . root@<pi>:/data/dbus-teslaapi-evcharger      # or git archive + scp
ssh root@<pi> 'cd /data/dbus-teslaapi-evcharger && sed -i "s/\r$//" *.sh *.py service/run TokenRefresh/*.sh TokenRefresh/*.py TokenRefresh/service/run && \
  bash install.sh && bash TokenRefresh/install.sh'
```

Then edit `/data/dbus-teslaapi-evcharger/config.ini` (`VehicleId`, `Deviceinstance`, `Phase`, `Position`) and restart. `install.sh` starts the service immediately, so expect log errors until config is right.

## Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| Service never starts, `svstat` shows down or 0 s | CRLF in `service/run`, or run not executable | `file service/run`; rerun the `sed` + `chmod` in step 3 |
| Crash-loop with `NameExistsException` | Orphaned old Python still holds the DBus name | `ps \| grep dbus-teslaapi`; `kill` the stray PID |
| `403 {"error":"account disabled: EXCEEDED_LIMIT"}` on every call | Tesla usage cap hit, not an auth problem | `curl -H "Authorization: Bearer $(cat /data/tesla/token.txt)" https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/users/me`. Raise the limit at developer.tesla.com → app → Billing and Usage, or wait for the cycle reset |
| `401` / token errors | Refresh token dead | `python3 bootstrap-tesla-token.py` on the Pi to mint a new one |
| `/vehicles` empty, `vehicle_data` 404 | Token minted for the wrong Tesla account | `/users/me` email must be the car owner's account |
| Start/stop charging fails, data reads work | Virtual key not paired, or public key not served at `/.well-known/appspecific/com.tesla.3p.public-key.pem` | See [04-tesla-integration.md](04-tesla-integration.md) |
| Files reverted to old code | Someone ran `update.sh` | Redeploy with `scp` |
