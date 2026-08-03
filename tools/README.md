# Capture tools

Some of the head unit's features only show themselves on the wire while the car
is **online** — ignition on *and* reaching the Teyes cloud over its own
internet (a data SIM, or a phone hotspot it joins). Ignition alone is not
enough; while the unit is offline it publishes nothing. These tools record what
the car sends so new entities can be built from real data.

## Live telemetry — `capture.py`

Waits for the car to come online, then subscribes to its MQTT topic and prints
every message it publishes, decoded.

```bash
python tools/capture.py --token <ucenterToken> --uid <uid> --imei <deviceId>
```

Read the three values from a signed-in browser at `https://online.teyes.cn`
(developer console):

```js
localStorage.getItem('ucenterToken')
localStorage.getItem('uid')
localStorage.getItem('imei')
```

Requires `aiohttp` and `cryptography` (`pip install aiohttp cryptography`). It
imports the integration's own crypto and MQTT code, so what it captures is
exactly what Home Assistant would see. Options: `--once` prints the current
state and exits, `--minutes N` sets how long to listen.

Paste the captured JSON into an issue — that is what turns "the parser missed a
field" into a proper sensor.

## Remote control methods — from the browser

Radio, wallpaper and the other controls live under the car API hosts
(`track.`, `acc.`, `msg.`, `file.`, `update.` …), but their method names are
obfuscated in the web bundle, so they cannot be read statically. Capture them
from real traffic instead, while the car is online:

1. Sign in at `https://online.teyes.cn` and open the car.
2. Open the browser's Network panel, filter to `carv3/car`.
3. Use the feature in the portal (open a wallpaper, a radio station, a live
   view). Each action fires one request; its path is the method name.
4. The request body is the same encrypted `data` field this integration
   already speaks, so once you have the path, `teyes.api_call` can call it:

   ```yaml
   action: teyes.api_call
   data:
     api: track        # the host segment the request used
     path: /<captured-method>
     params: { }       # whatever fields the portal sent
   response_variable: result
   ```

Post the method name, its host and its parameters in an issue and it can become
a switch, a button or a media control.

## Video

Whether a CC4 Pro exposes any remote video stream is still unknown; it may be
local-only. If, while the car is online, the portal's live view fires a request
under one of the car hosts (look for `webrtc`, `rtmp`, `stream`, `play`,
`live`), capture it as above — that request is the thread to pull.
