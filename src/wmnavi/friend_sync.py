"""Share live raid positions with friends via a public MQTT room code."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass

import paho.mqtt.client as mqtt

# Public broker — room code is the only access gate (accepted by design).
MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
TOPIC_ROOT = "wmnavi/v1"
STALE_SECONDS = 60.0

_ROOM_RE = re.compile(r"[^A-Z0-9\-]")


def normalize_room_code(raw: str) -> str:
    text = (raw or "").strip().upper().replace(" ", "-")
    text = _ROOM_RE.sub("", text)
    return text[:24]


def new_player_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class FriendPing:
    player_id: str
    name: str
    color: str
    map_slug: str
    x: float
    y: float
    z: float
    yaw_deg: float
    ts: float

    def is_stale(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return (now - self.ts) > STALE_SECONDS


class FriendSync:
    """MQTT room client. Callbacks fire from the network thread — marshal to UI."""

    def __init__(self, player_id: str, on_update=None, on_status=None, on_join_result=None):
        self.player_id = player_id or new_player_id()
        self.on_update = on_update
        self.on_status = on_status
        self.on_join_result = on_join_result
        self.room = ""
        self.name = "Operator"
        self.color = "#38bdf8"
        self._friends: dict[str, FriendPing] = {}
        self._lock = threading.Lock()
        self._client: mqtt.Client | None = None
        self._connected = False
        self._last_pos: dict | None = None

    def is_connected(self) -> bool:
        return bool(self._connected and self.room)

    def friends_snapshot(self) -> dict[str, FriendPing]:
        now = time.time()
        with self._lock:
            for pid in [p for p, ping in self._friends.items() if ping.is_stale(now)]:
                self._friends.pop(pid, None)
            return dict(self._friends)

    def live_count(self, map_slug: str | None = None) -> int:
        snaps = self.friends_snapshot()
        if not map_slug:
            return len(snaps)
        return sum(1 for p in snaps.values() if p.map_slug == map_slug)

    def _emit_status(self, text: str):
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _emit_update(self):
        if self.on_update:
            try:
                self.on_update(self.friends_snapshot())
            except Exception:
                pass

    def _topic(self, player_id: str | None = None) -> str:
        return f"{TOPIC_ROOT}/{self.room}/{player_id or self.player_id}"

    def _make_client(self, client_id: str) -> mqtt.Client:
        try:
            return mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=client_id,
                protocol=mqtt.MQTTv311,
            )
        except Exception:
            return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)

    def join(self, room: str, name: str, color: str) -> bool:
        code = normalize_room_code(room)
        if len(code) < 3:
            self._emit_status("Room code too short (min 3)")
            return False
        self.leave()
        self.room = code
        self.name = (name or "Operator").strip()[:24] or "Operator"
        self.color = color if str(color).startswith("#") else f"#{color}"
        with self._lock:
            self._friends.clear()

        client_id = f"wmnavi-{self.player_id}-{int(time.time()) % 100000}"
        client = self._make_client(client_id)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        try:
            client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
            client.loop_start()
        except Exception as exc:
            self._client = None
            self.room = ""
            self._emit_status(f"Friend sync connect failed: {exc}")
            return False
        self._emit_status(f"Connecting to room {self.room}…")
        return True

    def leave(self):
        client = self._client
        room = self.room
        self._client = None
        self._connected = False
        if client and room:
            try:
                client.publish(f"{TOPIC_ROOT}/{room}/{self.player_id}", b"", qos=0, retain=True)
            except Exception:
                pass
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
        self.room = ""
        with self._lock:
            self._friends.clear()
        self._emit_update()

    def _on_connect(self, client, _userdata, _flags, rc):
        if int(rc) != 0:
            self._emit_status(f"Friend sync refused ({rc})")
            if self.on_join_result:
                try:
                    self.on_join_result(False, self.room)
                except Exception:
                    pass
            return
        self._connected = True
        client.subscribe(f"{TOPIC_ROOT}/{self.room}/#", qos=0)
        self._emit_status(f"In room {self.room}")
        if self.on_join_result:
            try:
                self.on_join_result(True, self.room)
            except Exception:
                pass
        if self._last_pos:
            self.publish_position(**self._last_pos)

    def _on_disconnect(self, client, _userdata, _rc):
        self._connected = False
        if self.room:
            self._emit_status(f"Room {self.room} · reconnecting…")

    def _on_message(self, _client, _userdata, msg):
        try:
            parts = msg.topic.split("/")
            if len(parts) < 4:
                return
            pid = parts[-1]
            if pid == self.player_id:
                return
            if not msg.payload:
                with self._lock:
                    self._friends.pop(pid, None)
                self._emit_update()
                return
            data = json.loads(msg.payload.decode("utf-8", errors="ignore"))
            ping = FriendPing(
                player_id=pid,
                name=str(data.get("name") or "Friend")[:24],
                color=str(data.get("color") or "#38bdf8"),
                map_slug=str(data.get("map") or ""),
                x=float(data.get("x") or 0),
                y=float(data.get("y") or 0),
                z=float(data.get("z") or 0),
                yaw_deg=float(data.get("yaw") or 0),
                ts=float(data.get("ts") or time.time()),
            )
            with self._lock:
                self._friends[pid] = ping
            self._emit_update()
        except Exception:
            pass

    def publish_position(
        self,
        *,
        map_slug: str,
        x: float,
        y: float,
        z: float,
        yaw_deg: float,
        name: str | None = None,
        color: str | None = None,
    ):
        if name:
            self.name = name.strip()[:24] or self.name
        if color:
            self.color = color if str(color).startswith("#") else f"#{color}"
        self._last_pos = {
            "map_slug": map_slug,
            "x": float(x),
            "y": float(y),
            "z": float(z),
            "yaw_deg": float(yaw_deg),
        }
        if not self.room or not self._client:
            return
        payload = {
            "name": self.name,
            "color": self.color,
            "map": map_slug,
            "x": float(x),
            "y": float(y),
            "z": float(z),
            "yaw": float(yaw_deg),
            "ts": time.time(),
        }
        try:
            self._client.publish(
                self._topic(),
                payload=json.dumps(payload, separators=(",", ":")),
                qos=0,
                retain=True,
            )
        except Exception:
            pass
