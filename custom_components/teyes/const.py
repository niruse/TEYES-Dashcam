"""Constants for the Teyes integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "teyes"
ATTRIBUTION: Final = "Data provided by Teyes Online"
MANUFACTURER: Final = "Teyes"

# Hosts. The REST API and the MQTT broker live on different names: the bundle's
# `bsonline` host only answers REST, telemetry rides `onlinev2`.
REST_BASE: Final = "https://bsonline.teyesos.com/yfteyesonline"
CLIENT_API: Final = f"{REST_BASE}/clientv3"
CAR_API: Final = f"{REST_BASE}/carv3/car"
MQTT_WS_URL: Final = "wss://onlinev2.teyesos.com/websocket"

# MQTT. The head unit publishes on one topic derived from the device id, and
# the broker speaks MQTT 3.1 (protocol name "MQIsdp", level 3).
MQTT_CLIENT_PREFIX: Final = "app_"
MQTT_TOPIC_PREFIX: Final = "carinfo_"
MQTT_KEEPALIVE: Final = 10

CONF_TOKEN: Final = "token"
CONF_UID: Final = "uid"
CONF_DEVICE_ID: Final = "device_id"
CONF_DEVICE_IDS: Final = "device_ids"
CONF_AUTH_MODE: Final = "auth_mode"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ENABLE_PUSH: Final = "enable_push"
CONF_COORD_SYSTEM: Final = "coordinate_system"

AUTH_MODE_PASSWORD: Final = "password"
AUTH_MODE_TOKEN: Final = "token"

COORD_WGS84: Final = "wgs84"
COORD_GCJ02: Final = "gcj02"
COORD_SYSTEMS: Final = [COORD_WGS84, COORD_GCJ02]

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
DEFAULT_ENABLE_PUSH: Final = True
DEFAULT_COORD_SYSTEM: Final = COORD_WGS84

SERVICE_API_CALL: Final = "api_call"

PLATFORMS: Final = ["binary_sensor", "device_tracker", "sensor"]

# Both keys are embedded in the web bundle, so a standalone client can encrypt
# its requests and read the replies. They are the vendor's, not a secret of
# this account: the request key is public and the "private" key only decrypts
# the AES session key the server hands back with every response.
TEYES_PUBLIC_KEY: Final = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArRG64ASJpKcXcWM44GN6
wDGGNGvZn8j5jUKPvKyUnC3QcKeXkkYDh8LV2VG0rYLIY6vY9gDCI0o7y1osm5Jt
favkFDgGIN/PtHEpZtEkJWPCfsqshFladShiXYZbhnYoe6P0Q2DHZ+5FFYrdZBCO
lTc2ZZhUBgEzDp636yZUtIfbr6zuyZoVkoZ3Y6TGf//GkF9Q2CsfZv1PGf6bXMcQ
rI+1JkCjZ7kQ9SWDZHOVR9PSjrnj6yjxTP5SIaIQ78ugPzmhuLL/cdHDNttpDJCe
OpnJ2PGzB261KX/tD3LQFbLL6yHDYNKfe49oraVVurgsrjSwfSyLeeT7C0Xw4RIr
FwIDAQAB
-----END PUBLIC KEY-----"""

TEYES_PRIVATE_KEY: Final = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCtEbrgBImkpxdx
YzjgY3rAMYY0a9mfyPmNQo+8rJScLdBwp5eSRgOHwtXZUbStgshjq9j2AMIjSjvL
Wiybkm19q+QUOAYg38+0cSlm0SQlY8J+yqyEWVp1KGJdhluGdih7o/RDYMdn7kUV
it1kEI6VNzZlmFQGATMOnrfrJlS0h9uvrO7JmhWShndjpMZ//8aQX1DYKx9m/U8Z
/ptcxxCsj7UmQKNnuRD1JYNkc5VH09KOuePrKPFM/lIhohDvy6A/OaG4sv9x0cM2
22kMkJ46mcnY8bMHbrUpf+0PctAVssvrIcNg0p97j2itpVW6uCyuNLB9LIt55PsL
RfDhEisXAgMBAAECggEAIWtOCZn2tjuptDRztCiheGFXx+GXiVo2jgdvWAB/RdU2
7bn+87jfT6TYxHkZiXO6nDx/zakB0+yutSq5eGUDDYRWq2zSCiENq4s6cmcqMqob
0X3zaC8U35CnxeV+4O0KDi3vqd8TQkdpQaqp63ui/L9HmsCaYPp0UHtnilnBe4Z7
IsRR8la+d/sos+GxdNXkZKSgN/Oo8MObVkFxcv43QeonnwNBHthYiascENBOIaMk
s/PgK6CzZlXN0pfO1uYy0Kn4CWERyGTi4tBtaoazpAmFlHqBP5V4oYFTG9A3HEUJ
9UGimL3J6Ghqf2bhdaY5zbiL+IhiUsyDFetJOCENeQKBgQDUtuJmE1AdX94hZJ/r
BPVaUd0G/lLYkbZDYjNnCYZdLvsfEEDHMOteTnxdbm0+M0mcVkiyx6RmNci9cnB9
Q52+DsOOF1UqGuNdAQWAGGLcoBGz2r2w4QCRsPXy4i2fO3WQZZR61doDtxJIiNDT
aiO1Tt/hx0dSuN2FJfKIlLHHOwKBgQDQSZPCXz/HJdHP5XD5sTpCmgulsytMoVvC
JbQHGq22BDzSQ6jAMdaCkOa7MW4oRWhxRHeAPqr6aTbU8cFL2EH/QuojLfUAqTrr
pdu7N3ViY+jZhyxOfkAsbs9FGlRIh/Gm2gRQkPN/Yc3sOCQsnQMXJLM5InAZB++I
Ij+e2RXF1QKBgDlmhJ+Q9JyIozOVqMXN17ugvalGdeq1H179n+tdGaIJrIBMbX6T
B+CyA0NfKiGPy3xhmEqOLyHZv6rHt+1FksqXx1OPnk63ZMVTIj3X1s28cCp9i5Sw
kuu9uhDp0e/XJrlpf/DJxoQcIdXq/ZcfXDVv7GBL20ZKVpS4kcMjhPnDAoGAVsyC
rfqhim1BTUDRXsyzebPWbLwIrEuGAJdTua4te++yJPGbLlTWjtyxuoqz3EZoauNc
ZW44NFiKSB1jLyc1AZoyFmz9b8QWUMNymUdYPKNkEtqY5TEJrzqSV7rMKtm0I5Mx
Px3Yy1nHxcvzjz/zXL6njYTDcDr1YCbdmmXfA+0CgYBqAQZVqJUeKdWm9X4QJHQq
T700dy6eq1ekJ0llxRMCKqFFYKK3sqBWXJOc1wxtu+TETc7L6fy1S327OGR6oZO7
vIkbsYiaA0T833aCZ19/hnhJ1nRIHSmXsUxre/W1UCgEEOC4ACZ8zqOGjROVBnhR
iHjo0Pgwdfhkx4tjuPY5WQ==
-----END PRIVATE KEY-----"""
