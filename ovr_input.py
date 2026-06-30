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

def _rotation_quat(m) -> tuple[float, float, float, float]:

    m00, m01, m02 = m[0][0], m[0][1], m[0][2]
    m10, m11, m12 = m[1][0], m[1][1], m[1][2]
    m20, m21, m22 = m[2][0], m[2][1], m[2][2]
    trace = m00 + m11 + m22

    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (m21 - m12) * s
        y = (m02 - m20) * s
        z = (m10 - m01) * s
    elif m00 > m11 and m00 > m22:
        s = 2.0 * math.sqrt(max(1.0 + m00 - m11 - m22, 1e-12))
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = 2.0 * math.sqrt(max(1.0 + m11 - m00 - m22, 1e-12))
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = 2.0 * math.sqrt(max(1.0 + m22 - m00 - m11, 1e-12))
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    return (w, x, y, z)

def _quat_mul(a: tuple, b: tuple) -> tuple[float, float, float, float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw*bw - ax*bx - ay*by - az*bz,
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
    )

def _quat_conj(q: tuple) -> tuple[float, float, float, float]:
    w, x, y, z = q
    return (w, -x, -y, -z)

def _quat_angle_deg(a, b) -> float:

    dot = sum(p * q for p, q in zip(a, b))
    dot = max(-1.0, min(1.0, abs(dot)))
    return math.degrees(2.0 * math.acos(dot))

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

        self.prox_threshold:          float = 0.30   
        self.impact_speed_threshold:  float = 1.50   
        self._prox_in_range:          bool  = False
        self._prev_ctrl_dist:         float = -1.0   
        self._prev_ctrl_time:         float = 0.0

        self.on_velocity:             Optional[Callable[[str, float], None]] = None
        self.on_foot_land:            Optional[Callable[[str],        None]] = None
        self.on_controller_proximity: Optional[Callable[[float],      None]] = None
        self.on_controller_impact:    Optional[Callable[[],           None]] = None
        self.on_button_press:         Optional[Callable[[str, str],   None]] = None
        self.on_button_release:       Optional[Callable[[str, str],   None]] = None

        self.shoulder_radius:       float = 0.18   
        self.shoulder_down_offset:  float = 0.30   
        self.shoulder_side_offset:  float = 0.20   
        self.shoulder_back_offset:  float = 0.15   

        self._hand_in_shoulder: dict[str, set[str]] = {"left": set(), "right": set()}

        self.on_shoulder_grab: Optional[Callable[[str], None]] = None

        self._live_rel: dict[str, dict | None] = {"left": None, "right": None}

    def capture_relative_pose(self) -> dict:

        rel = self._live_rel
        out: dict[str, dict | None] = {}
        for hand in ("left", "right"):
            v = rel.get(hand)
            out[hand] = {"pos": list(v["pos"]), "rot": list(v["rot"])} if v else None
        return out

    def pose_match(self, target: dict, pos_tol: float, rot_tol_deg: float) -> bool:

        if not target:
            return False
        defined = [h for h in ("left", "right") if target.get(h)]
        if not defined:
            return False
        rel = self._live_rel
        for hand in defined:
            live = rel.get(hand)
            if live is None:
                return False
            tgt = target[hand]
            dx = live["pos"][0] - tgt["pos"][0]
            dy = live["pos"][1] - tgt["pos"][1]
            dz = live["pos"][2] - tgt["pos"][2]
            if math.sqrt(dx*dx + dy*dy + dz*dz) > pos_tol:
                return False
            if _quat_angle_deg(live["rot"], tgt["rot"]) > rot_tol_deg:
                return False
        return True

    def list_trackers(self) -> list[TrackerInfo]:
        if not self._vr:
            print("[ovr] list_trackers: not connected")
            return []

        left_idx  = self._vr.getTrackedDeviceIndexForControllerRole(
            openvr.TrackedControllerRole_LeftHand)
        right_idx = self._vr.getTrackedDeviceIndexForControllerRole(
            openvr.TrackedControllerRole_RightHand)
        hand_indices = {i for i in (left_idx, right_idx) if i != _INVALID}

        out: list[TrackerInfo] = []
        print("[ovr] full device scan:")

        for idx in range(_MAX):

            try:
                connected = self._vr.isTrackedDeviceConnected(idx)
            except Exception:
                connected = False
            if not connected:
                continue

            cls    = self._vr.getTrackedDeviceClass(idx)
            serial = _get_str_prop(self._vr, idx, openvr.Prop_SerialNumber_String) or f"device_{idx}"
            model  = _get_str_prop(self._vr, idx, openvr.Prop_ModelNumber_String)

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

            if cls == openvr.TrackedDeviceClass_GenericTracker:
                out.append(TrackerInfo(idx, serial, model, cls))
                continue

            if cls == openvr.TrackedDeviceClass_Controller and idx not in hand_indices:
                print(f"         ^ unassigned Controller — included as tracker option")
                out.append(TrackerInfo(idx, serial, model, cls))

        if not out:
            print("[ovr] WARNING: no tracker candidates found in device scan above")
        return out

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

                hmd_pose = poses[0]
                if hmd_pose.bPoseIsValid:
                    hm = hmd_pose.mDeviceToAbsoluteTracking.m
                    hpos = (hm[0][3], hm[1][3], hm[2][3])
                    hquat_conj = _quat_conj(_rotation_quat(hm))

                    cols = (
                        (hm[0][0], hm[1][0], hm[2][0]),
                        (hm[0][1], hm[1][1], hm[2][1]),
                        (hm[0][2], hm[1][2], hm[2][2]),
                    )
                    new_rel: dict[str, dict | None] = {"left": None, "right": None}
                    for ctrl_idx, ctrl_hand in hand_map.items():
                        cp = poses[ctrl_idx]
                        if not cp.bPoseIsValid:
                            continue
                        cm   = cp.mDeviceToAbsoluteTracking.m
                        cpos = (cm[0][3], cm[1][3], cm[2][3])
                        dx, dy, dz = cpos[0]-hpos[0], cpos[1]-hpos[1], cpos[2]-hpos[2]
                        local_pos = (
                            cols[0][0]*dx + cols[0][1]*dy + cols[0][2]*dz,
                            cols[1][0]*dx + cols[1][1]*dy + cols[1][2]*dz,
                            cols[2][0]*dx + cols[2][1]*dy + cols[2][2]*dz,
                        )
                        rel_quat = _quat_mul(hquat_conj, _rotation_quat(cm))
                        new_rel[ctrl_hand] = {"pos": local_pos, "rot": rel_quat}
                    self._live_rel = new_rel
                else:
                    self._live_rel = {"left": None, "right": None}

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

                            in_range = dist <= self.prox_threshold
                            if in_range and not self._prox_in_range:
                                if self.on_controller_proximity:
                                    self.on_controller_proximity(dist)
                            self._prox_in_range = in_range

                            if self._prev_ctrl_dist >= 0.0:
                                dt = max(now - self._prev_ctrl_time, 1e-6)
                                closing_speed = (self._prev_ctrl_dist - dist) / dt
                                if closing_speed >= self.impact_speed_threshold:
                                    if self.on_controller_impact:
                                        self.on_controller_impact()

                                    self._prev_ctrl_dist = -1.0
                                    self._prev_ctrl_time = now
                                else:
                                    self._prev_ctrl_dist = dist
                                    self._prev_ctrl_time = now
                            else:
                                self._prev_ctrl_dist = dist
                                self._prev_ctrl_time = now

                if self.on_shoulder_grab is not None:
                    hmd_pose = poses[0]
                    if hmd_pose.bPoseIsValid:
                        hm = hmd_pose.mDeviceToAbsoluteTracking
                        hx, hy, hz = hm.m[0][3], hm.m[1][3], hm.m[2][3]

                        rx, ry, rz =  hm.m[0][0],  hm.m[1][0],  hm.m[2][0]
                        bx, by, bz =  hm.m[0][2],  hm.m[1][2],  hm.m[2][2]

                        down = self.shoulder_down_offset
                        side = self.shoulder_side_offset
                        back = self.shoulder_back_offset

                        shoulder_anchors = {
                            "left":  (hx - rx*side + bx*back,
                                      hy - down - ry*side + by*back,
                                      hz - rz*side + bz*back),
                            "right": (hx + rx*side + bx*back,
                                      hy - down + ry*side + by*back,
                                      hz + rz*side + bz*back),
                        }

                        r2 = self.shoulder_radius ** 2
                        for ctrl_idx, ctrl_hand in hand_map.items():
                            cp = poses[ctrl_idx]
                            if not cp.bPoseIsValid:
                                self._hand_in_shoulder[ctrl_hand] = set()
                                continue
                            cm = cp.mDeviceToAbsoluteTracking
                            cx, cy, cz = cm.m[0][3], cm.m[1][3], cm.m[2][3]
                            in_now: set[str] = set()
                            for shoulder, (ax, ay, az) in shoulder_anchors.items():
                                dx = cx - ax; dy = cy - ay; dz = cz - az
                                if dx*dx + dy*dy + dz*dz <= r2:
                                    in_now.add(shoulder)
                            self._hand_in_shoulder[ctrl_hand] = in_now

                event = openvr.VREvent_t()
                while self._vr.pollNextEvent(event):
                    hand     = hand_map.get(event.trackedDeviceIndex)
                    btn_name = _BTN_LOOKUP.get(event.data.controller.button)
                    if hand is None or btn_name is None:
                        continue
                    if event.eventType == openvr.VREvent_ButtonPress:

                        if btn_name == "grip" and self.on_shoulder_grab:
                            for shoulder in list(self._hand_in_shoulder.get(hand, ())):
                                self.on_shoulder_grab(shoulder)
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
