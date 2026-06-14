import math
import sys
import threading
import time
import traceback
from datetime import datetime
from typing import Callable, Optional

import openvr

try:
    import crash_handler
    _crash_available = True
except ImportError:
    _crash_available = False

BUTTONS: dict[str, int] = {
    "trigger":    openvr.k_EButton_SteamVR_Trigger,
    "grip":       openvr.k_EButton_Grip,
    "primary":    openvr.k_EButton_A,
    "secondary":  openvr.k_EButton_ApplicationMenu,
    "thumbstick": openvr.k_EButton_SteamVR_Touchpad,
}
_BTN_LOOKUP: dict[int, str] = {v: k for k, v in BUTTONS.items()}

_INVALID = openvr.k_unTrackedDeviceIndexInvalid
_MAX     = openvr.k_unMaxTrackedDeviceCount


def _get_str_prop(vr: openvr.IVRSystem, idx: int, prop) -> str:
    try:
        raw = vr.getStringTrackedDeviceProperty(idx, prop)
        s = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return s.strip()
    except Exception:
        return ""


class TrackerInfo:
    __slots__ = ("index", "serial", "model", "device_class")

    def __init__(self, index: int, serial: str, model: str = "", device_class=None):
        self.index        = index
        self.serial       = serial
        self.model        = model
        self.device_class = device_class

    def __str__(self) -> str:
        label = f"{self.model} — {self.serial}" if self.model else self.serial
        return f"{label} (idx {self.index})"


class OVRInput:
    def __init__(self):
        self._vr:    Optional[openvr.IVRSystem] = None
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.connected = False

        self.foot_left_index:  int | None = None
        self.foot_right_index: int | None = None
        self.foot_left_floor:  float = 0.10
        self.foot_right_floor: float = 0.10

        self._foot_grounded: dict[str, bool] = {"foot_left": False, "foot_right": False}

        # Controller proximity detection
        # on_controller_proximity(distance)  — fired every tick when within prox_threshold
        # on_controller_impact()             — fired once when closing speed >= impact_speed_threshold
        self.prox_threshold:          float = 0.30   # metres; set by UI
        self.impact_speed_threshold:  float = 1.50   # m/s closing speed; set by UI
        self._prox_in_range:          bool  = False
        self._prev_ctrl_dist:         float = -1.0   # -1 = unknown
        self._prev_ctrl_time:         float = 0.0

        self.on_velocity:             Optional[Callable[[str, float], None]] = None
        self.on_foot_land:            Optional[Callable[[str],        None]] = None
        self.on_controller_proximity: Optional[Callable[[float],      None]] = None
        self.on_controller_impact:    Optional[Callable[[],           None]] = None
        self.on_button_press:         Optional[Callable[[str, str],   None]] = None
        self.on_button_release:       Optional[Callable[[str, str],   None]] = None

    # ── Tracker enumeration ───────────────────────────────

    def list_trackers(self) -> list[TrackerInfo]:
        if not self._vr:
            print("[ovr] list_trackers: not connected")
            return []

        # Get the hand controller indices so we can exclude them
        left_idx  = self._vr.getTrackedDeviceIndexForControllerRole(
            openvr.TrackedControllerRole_LeftHand)
        right_idx = self._vr.getTrackedDeviceIndexForControllerRole(
            openvr.TrackedControllerRole_RightHand)
        hand_indices = {i for i in (left_idx, right_idx) if i != _INVALID}

        out: list[TrackerInfo] = []
        print("[ovr] full device scan:")

        for idx in range(_MAX):
            # isTrackedDeviceConnected is faster than GetTrackedDeviceClass for skipping empties
            try:
                connected = self._vr.isTrackedDeviceConnected(idx)
            except Exception:
                connected = False
            if not connected:
                continue

            cls    = self._vr.getTrackedDeviceClass(idx)
            serial = _get_str_prop(self._vr, idx, openvr.Prop_SerialNumber_String) or f"device_{idx}"
            model  = _get_str_prop(self._vr, idx, openvr.Prop_ModelNumber_String)

            # Print raw integer value of cls so we can see exactly what comes back
            try:
                cls_int = int(cls)
            except Exception:
                cls_int = repr(cls)

            cls_name = {
                0: "Invalid",
                1: "HMD",
                2: "Controller",
                3: "GenericTracker",
                4: "TrackingReference",
                5: "DisplayRedirect",
            }.get(cls_int if isinstance(cls_int, int) else -1, f"unknown({cls_int})")

            print(f"  [{idx}] {cls_name:20s}  raw={cls_int}  serial={serial!r}  model={model!r}")

            # Include GenericTrackers (class 3)
            if cls == openvr.TrackedDeviceClass_GenericTracker:
                out.append(TrackerInfo(idx, serial, model, cls))
                continue

            # Also include Controllers that are NOT the left/right hand —
            # these are tracker pucks set to "Held in Hand" role
            if cls == openvr.TrackedDeviceClass_Controller and idx not in hand_indices:
                print(f"         ^ unassigned Controller — included as tracker option")
                out.append(TrackerInfo(idx, serial, model, cls))

        if not out:
            print("[ovr] WARNING: no tracker candidates found in device scan above")
        return out

    # ── Connect / Disconnect ──────────────────────────────

    def connect(self) -> tuple[bool, Optional[str]]:
        if self.connected:
            return True, None
        try:
            self._vr = openvr.init(openvr.VRApplication_Background)
            self.connected = True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._poll, daemon=True, name="ovr-poll"
            )
            self._thread.start()
            return True, None
        except openvr.OpenVRError as e:
            self._vr = None
            return False, str(e)

    def disconnect(self):
        self._stop.set()
        self.connected = False
        try:
            openvr.shutdown()
        except Exception:
            pass
        self._vr = None

    # ── Background poll loop ──────────────────────────────

    def _hand_map(self) -> dict[int, str]:
        left  = self._vr.getTrackedDeviceIndexForControllerRole(
            openvr.TrackedControllerRole_LeftHand)
        right = self._vr.getTrackedDeviceIndexForControllerRole(
            openvr.TrackedControllerRole_RightHand)
        m: dict[int, str] = {}
        if left  != _INVALID: m[left]  = "left"
        if right != _INVALID: m[right] = "right"
        return m

    def _poll(self):
        while not self._stop.is_set():
            try:
                hand_map = self._hand_map()

                result = self._vr.getDeviceToAbsoluteTrackingPose(
                    openvr.TrackingUniverseStanding, 0.0, _MAX
                )
                poses = result[0] if isinstance(result, tuple) else result

                if self.on_velocity:
                    for idx, hand in hand_map.items():
                        pose = poses[idx]
                        if pose.bPoseIsValid:
                            v   = pose.vVelocity
                            mag = math.sqrt(v.v[0]**2 + v.v[1]**2 + v.v[2]**2)
                            self.on_velocity(hand, mag)

                    hmd = poses[0]
                    if hmd.bPoseIsValid:
                        v   = hmd.vVelocity
                        mag = math.sqrt(v.v[0]**2 + v.v[1]**2 + v.v[2]**2)
                        self.on_velocity("head", mag)

                foot_slots: list[tuple[str, int | None, float]] = [
                    ("foot_left",  self.foot_left_index,  self.foot_left_floor),
                    ("foot_right", self.foot_right_index, self.foot_right_floor),
                ]
                for foot_name, fidx, floor_h in foot_slots:
                    if fidx is None:
                        continue
                    pose = poses[fidx]
                    if not pose.bPoseIsValid:
                        continue

                    if self.on_velocity:
                        v   = pose.vVelocity
                        mag = math.sqrt(v.v[0]**2 + v.v[1]**2 + v.v[2]**2)
                        self.on_velocity(foot_name, mag)

                    m     = pose.mDeviceToAbsoluteTracking
                    y_pos = m.m[1][3]
                    on_ground    = y_pos <= floor_h
                    was_grounded = self._foot_grounded[foot_name]
                    if on_ground and not was_grounded:
                        if self.on_foot_land:
                            self.on_foot_land(foot_name)
                    self._foot_grounded[foot_name] = on_ground

                # ── Controller proximity ──
                if self.on_controller_proximity or self.on_controller_impact:
                    left_idx  = next((i for i, h in hand_map.items() if h == "left"),  None)
                    right_idx = next((i for i, h in hand_map.items() if h == "right"), None)
                    if left_idx is not None and right_idx is not None:
                        lp = poses[left_idx]
                        rp = poses[right_idx]
                        if lp.bPoseIsValid and rp.bPoseIsValid:
                            lm = lp.mDeviceToAbsoluteTracking
                            rm = rp.mDeviceToAbsoluteTracking
                            dx = lm.m[0][3] - rm.m[0][3]
                            dy = lm.m[1][3] - rm.m[1][3]
                            dz = lm.m[2][3] - rm.m[2][3]
                            dist = math.sqrt(dx*dx + dy*dy + dz*dz)
                            now  = time.monotonic()

                            # Proximity: fire once on enter, once on exit re-arm
                            in_range = dist <= self.prox_threshold
                            if in_range and not self._prox_in_range:
                                if self.on_controller_proximity:
                                    self.on_controller_proximity(dist)
                            self._prox_in_range = in_range

                            # Impact: detect fast closing speed (positive = getting closer)
                            if self._prev_ctrl_dist >= 0.0:
                                dt = max(now - self._prev_ctrl_time, 1e-6)
                                closing_speed = (self._prev_ctrl_dist - dist) / dt
                                if closing_speed >= self.impact_speed_threshold:
                                    if self.on_controller_impact:
                                        self.on_controller_impact()
                                    # Reset prev dist so a continuous slam doesn't re-fire
                                    self._prev_ctrl_dist = -1.0
                                    self._prev_ctrl_time = now
                                else:
                                    self._prev_ctrl_dist = dist
                                    self._prev_ctrl_time = now
                            else:
                                self._prev_ctrl_dist = dist
                                self._prev_ctrl_time = now

                event = openvr.VREvent_t()
                while self._vr.pollNextEvent(event):
                    hand     = hand_map.get(event.trackedDeviceIndex)
                    btn_name = _BTN_LOOKUP.get(event.data.controller.button)
                    if hand is None or btn_name is None:
                        continue
                    if event.eventType == openvr.VREvent_ButtonPress:
                        if self.on_button_press:
                            self.on_button_press(hand, btn_name)
                    elif event.eventType == openvr.VREvent_ButtonUnpress:
                        if self.on_button_release:
                            self.on_button_release(hand, btn_name)

            except Exception as e:
                tb = traceback.format_exc()
                print(f"[ovr] poll thread crashed:\n{tb}", file=sys.stderr)
                if _crash_available:
                    try:
                        crash_handler._handle(*sys.exc_info(), source="thread 'ovr-poll'")
                    except Exception:
                        pass
                self.connected = False
                break

            time.sleep(0.01)
