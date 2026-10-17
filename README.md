# Teyes Dashcam for Home Assistant

A custom integration for **Teyes** Android head units (built and tested against
a **CC4 Pro**) that connects to the Teyes Online cloud the same way the vendor's
web portal does.

It gives you each car's position, its ignition and online state, and whatever
else the head unit reports — read straight from the cloud, with no app running
on a phone. One account can hold several cars; you tick which ones to bring
into Home Assistant, and the position shows even while a car is parked.

> Unofficial. Teyes provides no public API; everything here was worked out from
> the portal's own web bundle and its traffic.

## ☕ Support

If you found this project helpful, you can [buy me a coffee](https://coff.ee/niruse)!

## 🚨 Disclaimer

> This is an unofficial integration. You are responsible for the use of your credentials and your
> data. The author and contributors take no responsibility for any issues, account restrictions or
> data loss. Use at your own risk.


## What you get

### Cameras

Each fitted lens becomes a camera entity (`camera.<car>_front_camera`, rear,
left, right). Opening one tells the head unit to start streaming and plays its
cloud feed — the integration uses the HLS URL the app itself plays, so Home
Assistant streams it directly with no extra add-on. Live video needs the car
online; the entity is there whenever the car is, and shows the feed once it is
streaming. Needs Home Assistant's built-in `ffmpeg` (present by default) for
still images.

Each selected car becomes its own device, named after the car (e.g. "Suzuki
scross 2014 Milana"), with:

| Entity | What it is |
| --- | --- |
| `device_tracker.<car>` | Where the car is — its parked position even while off |
| `binary_sensor.<car>_online` | Whether the head unit is talking to the cloud |
| `binary_sensor.<car>_ignition` | ACC on/off |
| `binary_sensor.<car>_sim` | Whether a SIM is present |
| `binary_sensor.<car>_privacy_mode` | Privacy mode on/off |
| `binary_sensor.<car>_update_available` | An app update is waiting on the unit |
| `binary_sensor.<car>_geofence` | Whether an electronic fence is set |
| `sensor.<car>_speed` | Speed, while the car is reporting |
| `sensor.<car>_heading` | Direction of travel |
| `sensor.<car>_ignition_off_since` | When the ignition was last switched off |
| `sensor.<car>_subscription_expiry` | When the cloud service expires |
| `sensor.<car>_cameras` | How many cameras are fitted (with the list) |
| `sensor.<car>_geofence_status` | Off / radius / area |
| `sensor.<car>_position_time` | Timestamp on the last GPS fix |
| `sensor.<car>_last_report` | When the last live message arrived |

### Controls

| Entity | What it does |
| --- | --- |
| `lock.<car>_screen` | Lock or unlock the head unit's screen (car online) |
| `button.<car>_update_location` | Ask the car to report its position now (car online) |
| `switch.<car>_stealth_mode` | Hide the car in shared views (cloud, works offline) |
| `button.<car>_disable_mobile_data` | Turn the car's 4G off via the cloud (disabled by default) |

The lock and locate buttons are unavailable while the car is offline, since it
cannot act on a command it never receives.

### Trips, tyres and more

- **Trip history** — a `Last trip` sensor (start/end/date/distance) and a
  `Recorded trips` count; the `teyes.get_trips` action returns recent trips with
  their recorded path.
- **Tyre pressure** — a sensor per wheel (for cars fitted with TPMS sensors),
  with temperature, alert and connection in the attributes.
- **Signal** and **Last alarm** sensors, fed by the car's live reports; a
  `teyes_alarm` event fires on each shake/impact.
- **SIM data plan** — a `Data remaining` sensor for SIMs registered with the
  data service.

### Actions

| Action | What it does |
| --- | --- |
| `teyes.get_trips` | Recent trips, newest first |
| `teyes.get_events` | Sentry / alarm events |
| `teyes.get_photo` | A captured photo's details (link, location) by id |
| `teyes.set_geofence` | Create/update a circular geofence |
| `teyes.list_geofences` / `delete_geofence` | Manage geofences |
| `teyes.send_navigation` | Send a destination to open in a map app |
| `teyes.send_command` / `api_call` | The escape hatches for anything not wrapped yet |

The parked position comes from the account's own car list, so the tracker
works without waiting for the car to come online. Altitude, satellites,
mileage and voltage entities exist too, disabled by default: whether the head
unit sends them is not yet known, so they are there to switch on once a car
has been seen reporting. Diagnostic entities cover the live connection, the
firmware / MCU / app versions, when the car was paired, and the raw last
message.

## Requirements

- Home Assistant 2024.11 or newer.
- A Teyes Online account with the car already added to it.
- The head unit needs **its own internet** — a data SIM or a phone hotspot it
  joins. Ignition alone does not put it online, and while it is offline the
  cloud reports it as offline and it sends nothing.

## Installing

**HACS** — add this repository as a custom repository of type *Integration*,
install *Teyes*, then restart Home Assistant.

**Manually** — copy `custom_components/teyes` into your `config/custom_components`
folder and restart.

Then go to *Settings → Devices & services → Add integration → Teyes*.

## Setting it up

Sign in with the **email and password** of the account you use on the Teyes
portal. Home Assistant keeps the session and renews it on its own when it
expires. Your cars are discovered automatically — you never type a device id —
and if the account has more than one car you tick the ones to add.

> **A warning about that portal:** it also stores `ucenterLoginPassword` — your
> account password — in `localStorage`. Do not tick "remember me", and do not
> reuse that password anywhere else.

## Options

- **Cars** — tick which of the account's cars this entry follows. Add or remove
  cars here at any time after setup.
- **Polling interval** — how often the car's cloud state is read. 60 seconds by
  default.
- **Listen for live reports** — keeps a socket open to the cloud so position and
  speed arrive as the car sends them, instead of waiting for the next poll.
- **Coordinate system** — leave on WGS-84. Set it to GCJ-02 only if the car
  shows up a few hundred metres off *inside China*, which is where that offset
  applies.

## The `teyes.api_call` action

The car API's method names are obfuscated in the vendor's bundle, so this
integration covers what has been confirmed to work rather than guessing. The
`teyes.api_call` action lets you try anything else without editing code:

```yaml
action: teyes.api_call
data:
  api: client
  path: /user/getCarMqState
response_variable: result
```

`api` picks the host: `client` is the account API, and `car`, `track`, `acc`,
`msg`, `file`, `update`, `ef` and `tmps` are the car API hosts your account was
assigned. `uid`, `imei`, `token`, language and timezone are filled in for you;
anything in `params` is added on top, and `device_id` chooses which car the
call is about (it defaults to the account's first). The decoded reply comes
back as the action's response.

If you find a method that returns something useful, please open an issue — it
can become a proper entity.

## How it works

Every request is a `POST` of one multipart field, `data`, holding
`base64(RSA(JSON(params)))`. Replies come back as `{"res": "A|B|C"}`, where A
and B are the AES key and IV under the same RSA key and C is the AES-CBC
ciphertext of the JSON. Both halves of the RSA key pair ship inside the
portal's own JavaScript, so a client can encrypt requests and read replies
without a browser. In the decoded envelope, `code: 1` means success and `code:
0` carries the reason in `msg`.

`clientv3/user/getCarMqState` returns the car's state along with a `server`
block: the broker address and this car's MQTT credentials. The head unit's live
data is then raw **MQTT 3.1 inside a WebSocket** — not something Home
Assistant's MQTT integration can be pointed at, so the four packet types needed
to listen are implemented here. The client id is `app_<device id>`, the
username is the device id and the password is the `apikey` from that `server`
block; the car publishes on `carinfo_<device id>`.

## Extending it — the features that need the car online

Live telemetry, remote control (radio, wallpaper), and any video ride paths
whose shapes are only visible while the head unit is actually online. Until a
car has been seen online, those cannot be built from guesswork. The
[`tools/`](tools) folder has a capture script and a short recipe for recording
what the car sends, so each becomes a real entity from real data.

- **The shape of the live message is not documented.** The parser looks for the
  field names these platforms normally use (`lat`/`lng`/`speed`/`direction` and
  a dozen variants, including fixed-point integers) and keeps the whole raw
  message in the `Last car message` diagnostic entity and in the integration's
  diagnostics download. `tools/capture.py` records it directly.
- **Only some controls so far.** Commands ride the same live socket as the
  telemetry — a small JSON message published to the car's topic. Lock/unlock
  and locate are wired up; `lockscreen`, `unlockscreen`, `app_location` and
  `close4g` are the confirmed commands. Radio, wallpaper and navigation live in
  the app's lazily-loaded screens, so their exact payloads have to be captured
  (see [`tools/README.md`](tools/README.md)) and can then be sent with
  `teyes.send_command` before becoming their own entities.
- **Live video is built** (see [Cameras](#cameras)). It plays the cloud HLS/FLV
  feed the app uses; the head unit also offers a WebRTC (Aliyun RTC) path, which
  is not needed since HLS plays directly. Full end-to-end video can only be seen
  with the car online and streaming.
