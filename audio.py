import queue
import random
import threading
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf

_MAX_VOICES = 32

_AUDIO_EXTS = {".wav", ".ogg", ".mp3", ".flac"}

FOLDER_PREFIX = "folder:"

def resolve_sfx_path(val: str) -> str:
    if not val:
        return ""
    if val.startswith(FOLDER_PREFIX):
        folder = Path(val[len(FOLDER_PREFIX):])
        if not folder.is_dir():
            return ""
        candidates = [
            str(p) for p in folder.iterdir()
            if p.suffix.lower() in _AUDIO_EXTS and p.is_file()
        ]
        if not candidates:
            return ""
        return random.choice(candidates)
    return val

def list_output_devices() -> list[tuple[int, str]]:
    return [
        (i, d["name"])
        for i, d in enumerate(sd.query_devices())
        if d["max_output_channels"] > 0
    ]

def list_input_devices() -> list[tuple[int, str]]:
    return [
        (i, d["name"])
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]

class _Voice:
    __slots__ = ("data", "pos", "volume", "loop", "done")

    def __init__(self, data: np.ndarray, volume: float, loop: bool):
        self.data   = data      
        self.pos    = 0
        self.volume = volume    
        self.loop   = loop
        self.done   = False

    def stop(self):
        self.done = True

class _DeviceMixer:

    def __init__(self, device: int, samplerate: int, channels: int):

        self._incoming: queue.SimpleQueue[_Voice] = queue.SimpleQueue()
        self._voices: list[_Voice] = []
        self._sr      = samplerate
        self._ch      = channels

        self._stream = sd.OutputStream(
            samplerate=samplerate,
            channels=channels,
            device=device,
            dtype="float32",
            callback=self._cb,
        )
        self._stream.start()

    def _cb(self, outdata, frames, time_info, status):

        try:
            while True:
                v = self._incoming.get_nowait()
                if len(self._voices) >= _MAX_VOICES:

                    for i, old in enumerate(self._voices):
                        if not old.loop:
                            self._voices.pop(i)
                            break
                    else:
                        self._voices.pop(0)
                self._voices.append(v)
        except queue.Empty:
            pass

        buf = np.zeros((frames, self._ch), dtype=np.float32)
        alive: list[_Voice] = []
        for v in self._voices:
            if v.done:
                continue
            remaining = frames
            out_pos   = 0
            while remaining > 0:
                avail = len(v.data) - v.pos
                take  = min(remaining, avail)
                src   = v.data[v.pos:v.pos + take]
                sc = src.shape[1]
                if sc >= self._ch:
                    buf[out_pos:out_pos + take] += src[:, :self._ch] * v.volume
                else:
                    for c in range(self._ch):
                        buf[out_pos:out_pos + take, c] += src[:, c % sc] * v.volume
                v.pos      += take
                out_pos    += take
                remaining  -= take
                if v.pos >= len(v.data):
                    if v.loop:
                        v.pos = 0
                    else:
                        v.done = True
                        break
            if not v.done:
                alive.append(v)
        self._voices = alive
        np.clip(buf, -1.0, 1.0, out=buf)
        outdata[:] = buf

    def add_voice(self, voice: _Voice):

        self._incoming.put_nowait(voice)

    def stop_all(self):

        try:
            while True:
                self._incoming.get_nowait()
        except queue.Empty:
            pass
        for v in self._voices:
            v.done = True
        self._voices.clear()

    def close(self):
        self.stop_all()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass

class AudioEngine:
    def __init__(self):
        self.device1: int | None = None
        self.device2: int | None = None

        self._mixers: dict[tuple[int, int], _DeviceMixer] = {}
        self._cache:  dict[str, tuple[np.ndarray, int]] = {}
        self._lock    = threading.Lock()

    def _load(self, filepath: str) -> tuple[np.ndarray, int]:
        if filepath not in self._cache:
            data, sr = sf.read(filepath, always_2d=True, dtype="float32")
            self._cache[filepath] = (data, sr)
        return self._cache[filepath]

    def _get_mixer(self, device: int, samplerate: int,
                   n_channels: int) -> _DeviceMixer | None:
        key = (device, samplerate)
        with self._lock:
            if key in self._mixers:
                return self._mixers[key]
            try:
                info = sd.query_devices(device)
                ch   = min(int(info["max_output_channels"]), 2)
                m    = _DeviceMixer(device, samplerate, ch)
                self._mixers[key] = m
                return m
            except Exception as e:
                print(f"[audio] mixer open failed device={device} sr={samplerate}: {e}")
                return None

    def _adapt_channels(self, data: np.ndarray, device: int) -> np.ndarray:
        try:
            ch = min(int(sd.query_devices(device)["max_output_channels"]), 2)
        except Exception:
            ch = 2
        src = data.shape[1]
        if src == ch:
            return data
        if src < ch:
            return np.tile(data, (1, ch))[:, :ch]
        return data[:, :ch]

    def play(self, filepath: str, volume: float = 1.0,
             loop: bool = False) -> list[_Voice]:
        if not filepath:
            return []
        try:
            data, sr = self._load(filepath)
        except Exception as e:
            print(f"[audio] load error {filepath!r}: {e}")
            return []

        devices = list(dict.fromkeys(
            d for d in (self.device1, self.device2) if d is not None
        ))

        voices: list[_Voice] = []
        for dev in devices:
            d_ch = self._adapt_channels(data, dev)
            mixer = self._get_mixer(dev, sr, d_ch.shape[1])
            if mixer is None:
                continue
            v = _Voice(d_ch, volume, loop)
            mixer.add_voice(v)
            voices.append(v)
        return voices

    def preload(self, filepaths: list[str]):
        def _worker():
            for fp in filepaths:
                if fp and fp not in self._cache:
                    try:
                        self._load(fp)
                    except Exception as e:
                        print(f"[audio] preload skip {fp!r}: {e}")
        threading.Thread(target=_worker, daemon=True, name="audio-preload").start()

    def stop_all(self):
        with self._lock:
            for m in self._mixers.values():
                m.stop_all()

    def close_mixers(self):
        with self._lock:
            for m in self._mixers.values():
                m.close()
            self._mixers.clear()

    def clear_cache(self):
        self._cache.clear()

    @property
    def _streams(self):
        return []

    @_streams.setter
    def _streams(self, val):
        pass        

class MicEngine:

    _RING_MS = 200

    def __init__(self, audio: AudioEngine,
                 input_device: int, output_device: int,
                 volume: float = 1.0):
        self._audio         = audio
        self._input_device  = input_device
        self._output_device = output_device
        self._volume        = volume
        self._in_stream:  sd.InputStream  | None = None
        self._out_stream: sd.OutputStream | None = None
        self._lock    = threading.Lock()
        self._running = False

        self._ring:      np.ndarray | None = None
        self._ring_write = 0
        self._ring_read  = 0
        self._ring_size  = 0
        self._out_ch     = 0

        self._resample_carry: np.ndarray | None = None

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._running:
                return True, ""
            try:
                in_info  = sd.query_devices(self._input_device)
                out_info = sd.query_devices(self._output_device)
            except Exception as e:
                return False, f"Device query failed: {e}"

            in_ch  = min(int(in_info["max_input_channels"]),   2)
            out_ch = min(int(out_info["max_output_channels"]), 2)
            in_sr  = int(in_info["default_samplerate"])
            out_sr = int(out_info["default_samplerate"])

            if in_ch == 0:
                return False, "Input device has no input channels"
            if out_ch == 0:
                return False, "Output device has no output channels"

            self._in_sr  = in_sr
            self._out_sr = out_sr
            self._in_ch  = in_ch
            self._out_ch = out_ch

            ring_frames = int(out_sr * self._RING_MS / 1000)

            p = 1
            while p < ring_frames:
                p <<= 1
            self._ring_size  = p
            self._ring_mask  = p - 1
            self._ring       = np.zeros((p, out_ch), dtype=np.float32)
            self._ring_write = 0
            self._ring_read  = 0
            self._resample_carry = np.zeros((0, out_ch), dtype=np.float32)

            try:
                self._in_stream = sd.InputStream(
                    device=self._input_device,
                    channels=in_ch,
                    samplerate=in_sr,
                    dtype="float32",
                    callback=self._in_cb,
                    blocksize=256,
                )
                self._out_stream = sd.OutputStream(
                    device=self._output_device,
                    channels=out_ch,
                    samplerate=out_sr,
                    dtype="float32",
                    callback=self._out_cb,
                    blocksize=256,
                )
                self._running = True
                self._in_stream.start()
                self._out_stream.start()
                return True, ""
            except Exception as e:
                self._running = False
                for s in (self._in_stream, self._out_stream):
                    if s:
                        try:
                            s.stop(); s.close()
                        except Exception:
                            pass
                self._in_stream = self._out_stream = None
                return False, str(e)

    def stop(self):
        with self._lock:
            if not self._running:
                return
            self._running = False
            for s in (self._in_stream, self._out_stream):
                if s:
                    try:
                        s.stop(); s.close()
                    except Exception:
                        pass
            self._in_stream = self._out_stream = None

    @property
    def running(self) -> bool:
        return self._running

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(2.0, volume))

    def set_devices(self, input_device: int, output_device: int):
        was_running = self._running
        self.stop()
        self._input_device  = input_device
        self._output_device = output_device
        if was_running:
            self.start()

    def _in_cb(self, indata: np.ndarray, frames: int, time_info, status):
        if not self._running:
            return

        src_ch = indata.shape[1]
        out_ch = self._out_ch
        if src_ch == out_ch:
            chunk = indata
        elif src_ch < out_ch:
            chunk = np.tile(indata, (1, (out_ch // src_ch) + 1))[:, :out_ch]
        else:
            chunk = indata[:, :out_ch]

        if self._in_sr != self._out_sr:
            carry = self._resample_carry
            if carry is not None and len(carry):
                chunk = np.concatenate([carry, chunk], axis=0)
            ratio   = self._out_sr / self._in_sr
            out_len = int(len(chunk) * ratio)
            if out_len < 1:
                self._resample_carry = chunk.copy()
                return
            old_x = np.linspace(0.0, 1.0, len(chunk))
            new_x = np.linspace(0.0, 1.0, out_len)
            resampled = np.empty((out_len, out_ch), dtype=np.float32)
            for c in range(out_ch):
                resampled[:, c] = np.interp(new_x, old_x, chunk[:, c])

            self._resample_carry = chunk[-1:].copy()
            chunk = resampled
        else:
            chunk = chunk.copy()

        vol = self._volume
        if vol != 1.0:
            chunk = chunk * vol
        np.clip(chunk, -1.0, 1.0, out=chunk)

        ring  = self._ring
        mask  = self._ring_mask
        size  = self._ring_size
        wp    = self._ring_write
        n     = len(chunk)

        avail_write = (self._ring_read - wp - 1) % size
        if n > avail_write:
            self._ring_read = (self._ring_read + (n - avail_write)) % size

        for i in range(n):
            ring[wp & mask] = chunk[i]
            wp += 1
        self._ring_write = wp % size

    def _out_cb(self, outdata: np.ndarray, frames: int, time_info, status):
        if not self._running:
            outdata[:] = 0
            return

        ring = self._ring
        mask = self._ring_mask
        rp   = self._ring_read
        wp   = self._ring_write

        avail = (wp - rp) % self._ring_size
        fill  = min(avail, frames)

        for i in range(fill):
            outdata[i] = ring[rp & mask]
            rp += 1
        self._ring_read = rp % self._ring_size

        if fill < frames:
            outdata[fill:] = 0
