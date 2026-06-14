import threading
import numpy as np
import sounddevice as sd
import soundfile as sf

_MAX_VOICES = 32   # hard cap per device; oldest non-looping voice evicted first


def list_output_devices() -> list[tuple[int, str]]:
    return [
        (i, d["name"])
        for i, d in enumerate(sd.query_devices())
        if d["max_output_channels"] > 0
    ]


# ──────────────────────────────────────── Voice ──

class _Voice:
    __slots__ = ("data", "pos", "volume", "loop", "done")

    def __init__(self, data: np.ndarray, volume: float, loop: bool):
        self.data   = data      # (n_samples, n_ch) float32
        self.pos    = 0
        self.volume = volume    # GIL-safe float write from main thread
        self.loop   = loop
        self.done   = False

    def stop(self):
        self.done = True


# ──────────────────────────────────────── DeviceMixer ──

class _DeviceMixer:

    def __init__(self, device: int, samplerate: int, channels: int):
        self._lock    = threading.Lock()
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
        buf = np.zeros((frames, self._ch), dtype=np.float32)

        with self._lock:
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
                    # channel-match: src may have fewer/more channels than buf
                    sc = src.shape[1]
                    if sc >= self._ch:
                        buf[out_pos:out_pos + take] += src[:, :self._ch] * v.volume
                    else:
                        # upmix: repeat columns
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

        # Clip to prevent distortion when many voices overlap
        np.clip(buf, -1.0, 1.0, out=buf)
        outdata[:] = buf

    def add_voice(self, voice: _Voice):
        with self._lock:
            # Evict oldest non-looping voice if at cap
            if len(self._voices) >= _MAX_VOICES:
                for i, v in enumerate(self._voices):
                    if not v.loop:
                        self._voices.pop(i)
                        break
                else:
                    # All looping — evict the oldest
                    self._voices.pop(0)
            self._voices.append(voice)

    def stop_all(self):
        with self._lock:
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


# ──────────────────────────────────────── AudioEngine ──

class AudioEngine:
    def __init__(self):
        self.device1: int | None = None
        self.device2: int | None = None
        self._mixers: dict[int, _DeviceMixer] = {}
        self._cache:  dict[str, tuple[np.ndarray, int]] = {}
        self._lock    = threading.Lock()

    # ── Internal ──────────────────────────────────────────

    def _load(self, filepath: str) -> tuple[np.ndarray, int]:
        if filepath not in self._cache:
            data, sr = sf.read(filepath, always_2d=True, dtype="float32")
            self._cache[filepath] = (data, sr)
        return self._cache[filepath]

    def _get_mixer(self, device: int, samplerate: int,
                   n_channels: int) -> _DeviceMixer | None:
        with self._lock:
            if device in self._mixers:
                return self._mixers[device]
            try:
                info = sd.query_devices(device)
                ch   = min(int(info["max_output_channels"]), 2)
                m    = _DeviceMixer(device, samplerate, ch)
                self._mixers[device] = m
                return m
            except Exception as e:
                print(f"[audio] mixer open failed device={device}: {e}")
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

    # ── Public ────────────────────────────────────────────

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

    # ── Legacy shim for MusicFrame ────────────────────────
    # MusicFrame accesses _streams and _Stream directly — keep a minimal shim
    # so it still works without changes.

    @property
    def _streams(self):
        return []   # MusicFrame uses this only to check liveness; voices are in mixers now

    @_streams.setter
    def _streams(self, val):
        pass        # ignore writes — voices live in mixers
