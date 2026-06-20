

import threading
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

    def start(self) -> tuple[bool, str]:
        if not _OSC_AVAILABLE:
            return False, "python-osc not installed (pip install python-osc)"
        if self.running:
            return True, ""
        try:
            d = Dispatcher()
            d.map("/avatar/parameters/*", self._handler)
            self._server = BlockingOSCUDPServer((OSC_HOST, OSC_LISTEN_PORT), d)
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True, name="osc-listen"
            )
            self._thread.start()
            self.running = True
            return True, ""
        except Exception as e:
            return False, str(e)

    def _handler(self, address: str, *args):
        name  = address.removeprefix("/avatar/parameters/")
        value = args[0] if args else None
        self.params[name] = value
        if self.on_param:
            self.on_param(name, value)

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self.running = False

    def send(self, param: str, value):
        if self.client:
            self.client.send_message(f"/avatar/parameters/{param}", value)

class Bind:
    

    __slots__ = ("hand", "btn", "param", "mode", "state",
                 "val_a", "val_b", "int_idx",
                 "float_a", "float_b")

    def __init__(self, hand: str, btn: str, param: str,
                 mode: str = "bool",
                 state: bool = False,
                 val_a: int = 0, val_b: int = 1,
                 int_idx: int = 0,
                 float_a: str = "1.0", float_b: str = "0.0"):
        self.hand    = hand
        self.btn     = btn
        self.param   = param
        self.mode    = mode      
        self.state   = state
        self.val_a   = val_a
        self.val_b   = val_b
        self.int_idx = int_idx   
        self.float_a = float_a
        self.float_b = float_b

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

    def fire(self, link: OSCLink):
        if self.mode == "int":
            self.int_idx = 1 - self.int_idx
            value = self.val_a if self.int_idx == 0 else self.val_b
            link.send(self.param, int(value))
        elif self.mode == "float":
            self.int_idx = 1 - self.int_idx
            raw = self.float_a if self.int_idx == 0 else self.float_b
            try:
                value = float(raw)
            except ValueError:
                value = 1.0 if self.int_idx == 0 else 0.0
            link.send(self.param, value)
        else:
            self.state = not self.state
            link.send(self.param, self.state)

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
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Bind":
        return cls(
            d["hand"], d["btn"], d["param"],
            mode    = d.get("mode", "bool"),
            state   = d.get("state", False),
            val_a   = d.get("val_a", 0),
            val_b   = d.get("val_b", 1),
            int_idx = d.get("int_idx", 0),
            float_a = str(d.get("float_a", "1.0")),
            float_b = str(d.get("float_b", "0.0")),
        )
