import socket
import threading
import time
from typing import Callable, Optional

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import BlockingOSCUDPServer
    from pythonosc.udp_client import SimpleUDPClient
    _OSC_AVAILABLE = True
except ImportError:
    _OSC_AVAILABLE = False

OSC_HOST        = "127.0.0.1"
OSC_LISTEN_PORT = 9001
OSC_SEND_PORT   = 9000

_WATCHDOG_INTERVAL = 5.0
_WATCHDOG_TIMEOUT  = 15.0

class OSCLink:

    def __init__(self):
        self._server: Optional["BlockingOSCUDPServer"] = None
        self._thread: Optional[threading.Thread] = None
        self.client: Optional["SimpleUDPClient"] = (
            SimpleUDPClient(OSC_HOST, OSC_SEND_PORT) if _OSC_AVAILABLE else None
        )
        self.running = False
        self.params: dict[str, object] = {}

        self.on_param: Optional[Callable[[str, object], None]] = None
        self.on_avatar_change: Optional[Callable[[str], None]] = None
        self.last_avatar_id: Optional[str] = None

        self._last_packet_time: float = 0.0
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()

    def start(self) -> tuple[bool, str]:
        if not _OSC_AVAILABLE:
            return False, "python-osc not installed (pip install python-osc)"
        if self.running:
            return True, ""
        ok, err = self._start_server()
        if ok:
            self._watchdog_stop.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, daemon=True, name="osc-watchdog"
            )
            self._watchdog_thread.start()
        return ok, err

    def _start_server(self) -> tuple[bool, str]:
        try:
            d = Dispatcher()
            d.map("/avatar/parameters/*", self._handler)
            d.map("/avatar/change", self._handler_avatar_change)
            srv = BlockingOSCUDPServer((OSC_HOST, OSC_LISTEN_PORT), d)
            sock = srv.socket
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
            self._server = srv
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True, name="osc-listen"
            )
            self._thread.start()
            self._last_packet_time = time.monotonic()
            self.running = True
            return True, ""
        except Exception as e:
            return False, str(e)

    def _handler(self, address: str, *args):
        self._last_packet_time = time.monotonic()
        name  = address.removeprefix("/avatar/parameters/")
        value = args[0] if args else None
        self.params[name] = value
        if self.on_param:
            self.on_param(name, value)

    def _handler_avatar_change(self, address: str, *args):
        self._last_packet_time = time.monotonic()
        avatar_id = args[0] if args else None
        if isinstance(avatar_id, str) and avatar_id:
            self.last_avatar_id = avatar_id
            if self.on_avatar_change:
                self.on_avatar_change(avatar_id)

    def _watchdog_loop(self):
        while not self._watchdog_stop.is_set():
            time.sleep(_WATCHDOG_INTERVAL)
            if self._watchdog_stop.is_set():
                break
            if not self.running:
                continue
            thread_dead = (self._thread is None or not self._thread.is_alive())
            if not thread_dead:
                continue
            print("[osc] watchdog: listener thread dead, restarting…")
            try:
                if self._server:
                    try:
                        self._server.shutdown()
                    except Exception:
                        pass
                    try:
                        self._server.socket.close()
                    except Exception:
                        pass
                    self._server = None
            except Exception:
                pass
            self.running = False
            time.sleep(0.5)
            ok, err = self._start_server()
            if not ok:
                print(f"[osc] watchdog: restart failed — {err}")

    def stop(self):
        self._watchdog_stop.set()
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.socket.close()
            except Exception:
                pass
        self.running = False

    def send(self, param: str, value):
        if self.client:
            try:
                self.client.send_message(f"/avatar/parameters/{param}", value)
            except Exception as e:
                print(f"[osc] send failed: {e}")

class Bind:

    __slots__ = (
        "hand", "btn", "param", "mode", "state",
        "val_a", "val_b", "int_idx",
        "float_a", "float_b",
        "trigger_mode", "delay", "invert",
        "_held", "_delay_timer",
    )

    def __init__(self, hand: str, btn: str, param: str,
                 mode: str = "bool",
                 state: bool = False,
                 val_a: int = 0, val_b: int = 1,
                 int_idx: int = 0,
                 float_a: str = "1.0", float_b: str = "0.0",
                 trigger_mode: str = "toggle",
                 delay: float = 1.0,
                 invert: bool = False):
        self.hand         = hand
        self.btn          = btn
        self.param        = param
        self.mode         = mode
        self.state        = state
        self.val_a        = val_a
        self.val_b        = val_b
        self.int_idx      = int_idx
        self.float_a      = float_a
        self.float_b      = float_b
        self.trigger_mode = trigger_mode
        self.delay        = delay
        self.invert       = invert
        self._held        = False
        self._delay_timer: Optional[threading.Timer] = None

    def _bool_send(self, link: "OSCLink", value: bool):
        self.state = value
        link.send(self.param, (not value) if self.invert else value)

    def current_display(self) -> str:
        if self.mode == "int":
            cur = self.val_a if self.int_idx == 0 else self.val_b
            nxt = self.val_b if self.int_idx == 0 else self.val_a
            return f"{cur} → {nxt}"
        if self.mode == "float":
            cur = self.float_a if self.int_idx == 0 else self.float_b
            nxt = self.float_b if self.int_idx == 0 else self.float_a
            return f"{cur} → {nxt}"
        return "TRUE" if self.state else "FALSE"

    def _send_value(self, link: "OSCLink", on: bool):
        if self.mode == "int":
            if on:
                self.int_idx = 1 - self.int_idx
            value = self.val_a if self.int_idx == 0 else self.val_b
            link.send(self.param, int(value))
        elif self.mode == "float":
            if on:
                self.int_idx = 1 - self.int_idx
            raw = self.float_a if self.int_idx == 0 else self.float_b
            try:
                value = float(raw)
            except ValueError:
                value = 1.0 if self.int_idx == 0 else 0.0
            link.send(self.param, value)
        else:
            self._bool_send(link, on)

    def fire(self, link: "OSCLink"):
        if self.trigger_mode == "toggle":
            self._cancel_timer()
            if self.mode == "bool":
                self._bool_send(link, not self.state)
            else:
                self._send_value(link, True)

        elif self.trigger_mode == "hold":
            self._cancel_timer()
            self._held = True
            self._send_value(link, True)

        elif self.trigger_mode == "delay":
            self._cancel_timer()
            if self.mode == "bool":
                self._bool_send(link, True)
            else:
                self._send_value(link, True)
            def _auto_off():
                if self.mode == "bool":
                    self._bool_send(link, False)
                else:
                    self._send_value(link, False)
            t = threading.Timer(max(self.delay, 0.05), _auto_off)
            t.daemon = True
            t.start()
            self._delay_timer = t

    def release(self, link: "OSCLink"):
        if self.trigger_mode == "hold" and self._held:
            self._held = False
            if self.mode == "bool":
                self._bool_send(link, False)
            else:
                self._send_value(link, False)

    def _cancel_timer(self):
        if self._delay_timer is not None:
            self._delay_timer.cancel()
            self._delay_timer = None

    def is_active(self) -> bool:
        if self.mode == "bool":
            return bool(self.state)
        return self.int_idx == 1

    def force_off(self, link: "OSCLink"):
        self._cancel_timer()
        self._held = False
        if self.mode == "int":
            self.int_idx = 0
            link.send(self.param, int(self.val_a))
        elif self.mode == "float":
            self.int_idx = 0
            try:
                value = float(self.float_a)
            except ValueError:
                value = 0.0
            link.send(self.param, value)
        else:
            self._bool_send(link, False)

    def label(self) -> str:
        hand_short = "L" if self.hand == "left" else "R"
        btn_pretty = self.btn.replace("thumbstick", "stick").title()
        return f"{hand_short} {btn_pretty}"

    def to_dict(self) -> dict:
        return {
            "hand": self.hand, "btn": self.btn, "param": self.param,
            "mode": self.mode, "state": self.state,
            "val_a": self.val_a, "val_b": self.val_b, "int_idx": self.int_idx,
            "float_a": self.float_a, "float_b": self.float_b,
            "trigger_mode": self.trigger_mode, "delay": self.delay,
            "invert": self.invert,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Bind":
        return cls(
            d["hand"], d["btn"], d["param"],
            mode         = d.get("mode", "bool"),
            state        = d.get("state", False),
            val_a        = d.get("val_a", 0),
            val_b        = d.get("val_b", 1),
            int_idx      = d.get("int_idx", 0),
            float_a      = str(d.get("float_a", "1.0")),
            float_b      = str(d.get("float_b", "0.0")),
            trigger_mode = d.get("trigger_mode", "toggle"),
            delay        = float(d.get("delay", 1.0)),
            invert       = bool(d.get("invert", False)),
        )

class ComboBind:

    __slots__ = (
        "buttons", "param", "mode", "state",
        "val_a", "val_b", "int_idx",
        "float_a", "float_b",
        "trigger_mode", "delay", "invert",
        "_held", "_delay_timer",
    )

    def __init__(self, buttons: list[tuple[str, str]], param: str,
                 mode: str = "bool",
                 state: bool = False,
                 val_a: int = 0, val_b: int = 1,
                 int_idx: int = 0,
                 float_a: str = "1.0", float_b: str = "0.0",
                 trigger_mode: str = "toggle",
                 delay: float = 1.0,
                 invert: bool = False):
        self.buttons       = list(buttons)
        self.param         = param
        self.mode          = mode
        self.state         = state
        self.val_a         = val_a
        self.val_b         = val_b
        self.int_idx       = int_idx
        self.float_a       = float_a
        self.float_b       = float_b
        self.trigger_mode  = trigger_mode
        self.delay         = delay
        self.invert        = invert
        self._held         = False
        self._delay_timer: Optional[threading.Timer] = None

    def _bool_send(self, link: "OSCLink", value: bool):
        self.state = value
        link.send(self.param, (not value) if self.invert else value)

    def current_display(self) -> str:
        if self.mode == "int":
            cur = self.val_a if self.int_idx == 0 else self.val_b
            nxt = self.val_b if self.int_idx == 0 else self.val_a
            return f"{cur} → {nxt}"
        if self.mode == "float":
            cur = self.float_a if self.int_idx == 0 else self.float_b
            nxt = self.float_b if self.int_idx == 0 else self.float_a
            return f"{cur} → {nxt}"
        return "TRUE" if self.state else "FALSE"

    def _send_value(self, link: "OSCLink", on: bool):
        if self.mode == "int":
            if on:
                self.int_idx = 1 - self.int_idx
            value = self.val_a if self.int_idx == 0 else self.val_b
            link.send(self.param, int(value))
        elif self.mode == "float":
            if on:
                self.int_idx = 1 - self.int_idx
            raw = self.float_a if self.int_idx == 0 else self.float_b
            try:
                value = float(raw)
            except ValueError:
                value = 1.0 if self.int_idx == 0 else 0.0
            link.send(self.param, value)
        else:
            self._bool_send(link, on)

    def fire(self, link: "OSCLink"):
        if self.trigger_mode == "toggle":
            self._cancel_timer()
            if self.mode == "bool":
                self._bool_send(link, not self.state)
            else:
                self._send_value(link, True)

        elif self.trigger_mode == "hold":
            self._cancel_timer()
            self._held = True
            self._send_value(link, True)

        elif self.trigger_mode == "delay":
            self._cancel_timer()
            if self.mode == "bool":
                self._bool_send(link, True)
            else:
                self._send_value(link, True)
            def _auto_off():
                if self.mode == "bool":
                    self._bool_send(link, False)
                else:
                    self._send_value(link, False)
            t = threading.Timer(max(self.delay, 0.05), _auto_off)
            t.daemon = True
            t.start()
            self._delay_timer = t

    def release(self, link: "OSCLink"):
        if self.trigger_mode == "hold" and self._held:
            self._held = False
            if self.mode == "bool":
                self._bool_send(link, False)
            else:
                self._send_value(link, False)

    def _cancel_timer(self):
        if self._delay_timer is not None:
            self._delay_timer.cancel()
            self._delay_timer = None

    def is_active(self) -> bool:
        if self.mode == "bool":
            return bool(self.state)
        return self.int_idx == 1

    def force_off(self, link: "OSCLink"):
        self._cancel_timer()
        self._held = False
        if self.mode == "int":
            self.int_idx = 0
            link.send(self.param, int(self.val_a))
        elif self.mode == "float":
            self.int_idx = 0
            try:
                value = float(self.float_a)
            except ValueError:
                value = 0.0
            link.send(self.param, value)
        else:
            self._bool_send(link, False)

    def label(self) -> str:
        parts = []
        for hand, btn in self.buttons:
            hand_short = "L" if hand == "left" else "R"
            btn_pretty = btn.replace("thumbstick", "stick").title()
            parts.append(f"{hand_short} {btn_pretty}")
        return " + ".join(parts) if parts else "(no buttons)"

    def to_dict(self) -> dict:
        return {
            "buttons": [[h, b] for h, b in self.buttons],
            "param": self.param,
            "mode": self.mode, "state": self.state,
            "val_a": self.val_a, "val_b": self.val_b, "int_idx": self.int_idx,
            "float_a": self.float_a, "float_b": self.float_b,
            "trigger_mode": self.trigger_mode, "delay": self.delay,
            "invert": self.invert,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ComboBind":
        buttons = [tuple(x) for x in d.get("buttons", [])]
        return cls(
            buttons, d.get("param", ""),
            mode         = d.get("mode", "bool"),
            state        = d.get("state", False),
            val_a        = d.get("val_a", 0),
            val_b        = d.get("val_b", 1),
            int_idx      = d.get("int_idx", 0),
            float_a      = str(d.get("float_a", "1.0")),
            float_b      = str(d.get("float_b", "0.0")),
            trigger_mode = d.get("trigger_mode", "toggle"),
            delay        = float(d.get("delay", 1.0)),
            invert       = bool(d.get("invert", False)),
        )
