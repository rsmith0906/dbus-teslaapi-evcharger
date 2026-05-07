# 03c — Can I add a custom side-panel widget like CPU / Inverter / Weather?

Short answer: **Yes, for any of the device classes VRM already renders cards for.** You can't invent a brand-new card type, but the bar is much lower than I first thought — both the "CPU" and "Inverter" cards in your screenshot are *already* community-driver-published. So this is a solved pattern.

## What those side cards actually are

The right-column cards are **VRM Portal cloud widgets**. They live in Victron's React app at `vrm.victronenergy.com` and bind to known DBus device classes. Crucially, the cards key off **device class**, not vendor identity — anything matching a recognized `com.victronenergy.<role>.*` service of a class with a card template will render automatically.

In your screenshot, all three cards are produced this way:

| Card | What's actually publishing it |
|------|------------------------------|
| **CPU** | `com.victronenergy.temperature.RPi_cpu_id01`, published by [`Rikkert-RS/VenusOS-TemperatureService`](https://github.com/Rikkert-RS/VenusOS-TemperatureService) reading `/sys/devices/virtual/thermal/thermal_zone0/temp`. Not a native Victron CPU widget — `/CustomName="Raspberry Pi OS"` happens to be configurable. |
| **Inverter** | A `com.victronenergy.temperature.Wire_idNN` instance also from `VenusOS-TemperatureService`, fed by a DS18B20 1-wire sensor zip-tied to your inverter housing, with `/CustomName="Inverter"` and `/TemperatureType` set appropriately. |
| **Weather** | The only one that's truly Victron-side: resolved server-side from your install's geolocation. Not driven by any DBus service. |

So both CPU and Inverter are *exactly* the pattern this driver could use for Tesla telemetry. The community package proves the mechanism.

## How `VenusOS-TemperatureService` does it

The relevant lines from `dbus-i2c.py`:

```python
base = 'com.victronenergy'

def new_service(base, type, physical, logical, id, instance, settingId=False):
    self = VeDbusService("{}.{}.{}_id{:02d}".format(base, type, physical, id), dbusconnection())
    # ... mandatory paths ...
    if type == 'temperature':
        self.add_path('/Temperature', [])
        self.add_path('/Status', 0)
        self.add_path('/TemperatureType', 0, writeable=True, ...)   # 0..5 enum
        self.add_path('/CustomName', '', writeable=True, ...)
        self.add_path('/Function', 1, writeable=True)
    return self

# CPU temperature
dbusservice['cpu-temp'] = new_service(base, 'temperature', 'RPi_cpu', 'Raspberry Pi OS',
                                       SCount+1, 100, SCount+1)

# Each detected DS18B20 1-wire sensor
dbusservice['W1-temp:'+id] = new_service(base, 'temperature', 'Wire', '1Wire',
                                          SCount+1, 100+SCount, deviceID)
```

Three things to notice:

1. **Service-name pattern**: `com.victronenergy.temperature.<physical>_id<NN>`. The `<physical>` part is just a label (`RPi_cpu`, `Wire`, `i2c`); the `_id<NN>` ensures uniqueness when multiple sensors are attached.
2. **Per-instance settings**: `/CustomName` is writeable and persists via Victron's `SettingsDevice` API. So the user names "Inverter" once, in the GX GUI Settings page, and it sticks.
3. **`/TemperatureType` is the icon hint**: `0=battery, 1=fridge, 2=generic, 3=room, 4=outdoor, 5=water/coolant`. VRM and the GX GUI render slightly different icons depending on this.

## What this means for the Tesla driver

You can publish Tesla temperatures as side cards with a small addition to `dbus-teslaapi-evcharger.py`. Each instance gets its own card.

### Concrete code skeleton

```python
class TeslaTemperatureService:
    def __init__(self, name_suffix, custom_name, temp_type, instance, get_value):
        self._service = VeDbusService(
            f"com.victronenergy.temperature.tesla_{name_suffix}_id{instance:02d}"
        )
        self._service.add_path('/Mgmt/ProcessName', __file__)
        self._service.add_path('/Mgmt/ProcessVersion',
                               'Tesla telemetry on Python ' + platform.python_version())
        self._service.add_path('/Mgmt/Connection', 'Tesla API')
        self._service.add_path('/DeviceInstance', instance)
        self._service.add_path('/ProductId', 0xFFFF)
        self._service.add_path('/ProductName', 'Tesla telemetry')
        self._service.add_path('/CustomName', custom_name, writeable=True)
        self._service.add_path('/FirmwareVersion', 0)
        self._service.add_path('/HardwareVersion', 0)
        self._service.add_path('/Connected', 1)
        self._service.add_path('/Temperature', 0.0)
        self._service.add_path('/TemperatureType', temp_type, writeable=True)
        self._service.add_path('/Function', 1, writeable=True)
        self._service.add_path('/Status', 0)
        self._get_value = get_value

    def update(self, car_data):
        try:
            self._service['/Temperature'] = round(self._get_value(car_data), 1)
            self._service['/Connected'] = 1
            self._service['/Status'] = 0
        except (KeyError, TypeError):
            self._service['/Connected'] = 0
            self._service['/Status'] = 1
```

In `DbusTeslaAPIService.__init__`, instantiate one per metric you want surfaced:

```python
self._temps = [
    TeslaTemperatureService(
        "battery", "Tesla battery", temp_type=0, instance=70,
        get_value=lambda d: d['response']['climate_state'].get('battery_heater_no_power')
        # or pull from the more reliable path of your choice
    ),
    TeslaTemperatureService(
        "inside", "Tesla cabin", temp_type=3, instance=71,
        get_value=lambda d: c_to_f(d['response']['climate_state']['inside_temp'])
    ),
    TeslaTemperatureService(
        "outside", "Tesla outside", temp_type=4, instance=72,
        get_value=lambda d: c_to_f(d['response']['climate_state']['outside_temp'])
    ),
]
```

Then in `_update`, after a successful `vehicle_data` fetch:

```python
for t in self._temps:
    t.update(self._carData)
```

Result: three new side cards appear in VRM — "Tesla battery", "Tesla cabin", "Tesla outside" — with their own min/max-over-24h history strips, just like the Inverter card you already have.

### Notes on `vehicle_data` temperature fields

What's actually in the JSON, with units:

| JSON field | Description | Unit |
|------------|-------------|------|
| `climate_state.inside_temp` | Cabin temperature | °C |
| `climate_state.outside_temp` | Outside ambient (sensor on car) | °C |
| `climate_state.driver_temp_setting` | Driver-side climate setpoint | °C |
| `climate_state.passenger_temp_setting` | Passenger-side setpoint | °C |
| `vehicle_state.battery_heater` | bool — heater currently on | — |
| `charge_state.battery_heater_on` | bool — heater on while charging | — |

There's no direct `battery_temp` in `vehicle_data`. If you want true pack temperature you need the `endpoints` API or a separate scrape. But cabin and outside are right there.

`/Temperature` is in **°C** by Victron convention; the GUI/VRM converts to °F based on user preferences. So `inside_temp` lands directly without conversion — drop the `c_to_f` calls in the example above.

## Other device classes that get side cards

Same pattern works for any of these — register a `com.victronenergy.<role>.<instance>` service with the right paths and a card appears:

| Class | DBus service prefix | Useful for |
|-------|---------------------|-----------|
| `temperature` | `com.victronenergy.temperature.*` | battery, cabin, outside temps (covered above) |
| `tank` | `com.victronenergy.tank.*` | repurpose for "fuel level" → battery SoC as a percent |
| `humidity` | `com.victronenergy.humidity.*` | rare, but works |
| `meteo` | `com.victronenergy.meteo.*` | added 2025-09-17 — irradiance, wind, ambient |
| `gps` | `com.victronenergy.gps.*` | car location → Position card; system speed |
| `battery` | `com.victronenergy.battery.*` | car drive battery — be careful: dbus-systemcalc may roll into system totals; set `/AllowedRoles` to limit |

For Tesla specifically the most valuable adds are:

- `com.victronenergy.temperature.tesla_cabin_id71` → "Tesla cabin" side card
- `com.victronenergy.temperature.tesla_outside_id72` → "Tesla outside" side card
- `com.victronenergy.gps.tesla_id80` → puts the car on the map widget (location, speed)
- Possibly a `com.victronenergy.battery.*` for SoC, with caveats

## What you still cannot do

- **Invent a brand-new card type.** A card titled "Tesla Charge" with bespoke layout requires Victron to ship a release. The 2025-05-06 changelog adding "EV Location, EV SOC, EV State" is exactly an example of Victron doing this for the EVCS class — they added new paths that the existing EVCS card now reads.
- **Bundle React/QML for VRM with your driver.** VRM doesn't load anything from your device.
- **Have arbitrary `/Tesla/*` paths show up in Advanced Dashboard.** Custom paths on a recognized service are still ignored by VRM ingestion. Whatever you want graphed must live on a path VRM expects (e.g., `/Temperature` on a `temperature` service).

## Recommended next step

Add three temperature services and one GPS service to the driver. Roughly 80 lines of code, no frontend changes, and you get four new cards in the VRM side panel plus the car as a pin on the dashboard map. References:

- [VRM Portal Changelog](https://www.victronenergy.com/live/vrm_portal:change_log) — confirm what cards exist this month.
- [Rikkert-RS/VenusOS-TemperatureService — `dbus-i2c.py`](https://github.com/Rikkert-RS/VenusOS-TemperatureService/blob/master/dbus-i2c.py) — copy the `new_service()` helper and `SettingsDevice` integration.
- [Victron `velib_python` — `vedbus.py`](https://github.com/victronenergy/velib_python/blob/master/vedbus.py) — full API of `VeDbusService`.
- [DBus paths reference — temperature, gps, tank](https://github.com/victronenergy/venus/wiki/dbus) — required path set per class.
