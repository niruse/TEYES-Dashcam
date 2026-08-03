# Teyes for Home Assistant

A custom integration for **Teyes** Android head units (built and tested against
a **CC4 Pro**) that connects to the Teyes Online cloud the same way the vendor's
web portal does.

It gives you each car's position, its ignition and online state, and whatever
else the head unit reports — read straight from the cloud, with no app running
on a phone. One account can hold several cars; you tick which ones to bring
into Home Assistant, and the position shows even while a car is parked.

> Unofficial. Teyes provides no public API; everything here was worked out from
> the portal's own web bundle and its traffic.

## What you get

Each selected car becomes its own device, named after the car (e.g. "Suzuki
scross 2014 Milana"), with:

| Entity | What it is |
| --- | --- |
| `device_tracker.<car>` | Where the car is — its parked position even while off |
| `binary_sensor.<car>_online` | Whether the head unit is talking to the cloud |
| `binary_sensor.<car>_ignition` | ACC on/off |
| `sensor.<car>_speed` | Speed, while the car is reporting |
| `sensor.<car>_heading` | Direction of travel |
| `sensor.<car>_ignition_off_since` | When the ignition was last switched off |
| `sensor.<car>_position_time` | Timestamp on the last GPS fix |
| `sensor.<car>_last_report` | When the last live message arrived |

The parked position comes from the account's own car list, so the tracker
works without waiting for the car to come online. Altitude, satellites,
mileage and voltage entities exist too, disabled by default: whether the head
unit sends them is not yet known, so they are there to switch on once a car
has been seen reporting. Diagnostic entities cover the live connection, the
fitted-camera flags, and the raw last message.

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

There are two ways in. Either way, your cars are discovered automatically —
you never type a device id — and if the account has more than one car you tick
the ones to add.

**Email and password** — the account you use on the Teyes portal. Home
Assistant keeps the session and renews it on its own when it expires.

**Session token (advanced)** — for when the portal itself is unreachable but
the API behind it is still up, which does happen. Sign in to
`https://online.teyes.cn` in a browser, open the developer console and read:

```js
localStorage.getItem('ucenterToken')   // the session token
localStorage.getItem('uid')            // the account id, e.g. email12345
```

A token lasts a few days; when it expires Home Assistant asks for a new one.

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

## Known gaps

- **The shape of the live message is not documented.** The parser looks for the
  field names these platforms normally use (`lat`/`lng`/`speed`/`direction` and
  a dozen variants, including fixed-point integers) and keeps the whole raw
  message in the `Last car message` diagnostic entity and in the integration's
  diagnostics download. If your car reports something the parser misses, that
  download is exactly what is needed to add it.
- **No video.** Whether a CC4 Pro exposes any stream remotely is still an open
  question; it may be local-only.
- **No controls yet** (wallpaper, radio). Those endpoints exist but their
  method names are obfuscated — `teyes.api_call` is how they get found.
