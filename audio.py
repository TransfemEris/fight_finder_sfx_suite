import threading
import numpy as np
import sounddevice as sd
import soundfile as sf

_MAX_VOICES = 32

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
        with self._lock:
            if len(self._voices) >= _MAX_VOICES:
                for i, v in enumerate(self._voices):
                    if not v.loop:
                        self._voices.pop(i)
                        break
                else:
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

class AudioEngine:
    def __init__(self):
        self.device1: int | None = None
        self.device2: int | None = None
        self._mixers: dict[int, _DeviceMixer] = {}
        self._cache:  dict[str, tuple[np.ndarray, int]] = {}
        self._lock    = threading.Lock()

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

    def __init__(self, audio: AudioEngine,
                 input_device: int, output_device: int,
                 volume: float = 1.0):
        self._audio         = audio
        self._input_device  = input_device
        self._output_device = output_device
        self._volume        = volume
        self._stream: sd.InputStream | None = None
        self._lock          = threading.Lock()
        self._running       = False
        self._mixer: _DeviceMixer | None = None

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._running:
                return True, ""
            try:
                in_info  = sd.query_devices(self._input_device)
                out_info = sd.query_devices(self._output_device)
            except Exception as e:
                return False, f"Device query failed: {e}"

            in_ch   = min(int(in_info["max_input_channels"]),  2)
            out_ch  = min(int(out_info["max_output_channels"]), 2)
            in_sr   = int(in_info["default_samplerate"])
            out_sr  = int(out_info["default_samplerate"])

            if in_ch == 0:
                return False, "Input device has no input channels"
            if out_ch == 0:
                return False, "Output device has no output channels"

            self._mixer = self._audio._get_mixer(self._output_device, out_sr, out_ch)
            if self._mixer is None:
                return False, "Could not open output mixer"
            self._in_sr  = in_sr
            self._out_sr = out_sr
            self._in_ch  = in_ch
            self._out_ch = out_ch
            self._resample_buf = np.zeros((0, out_ch), dtype=np.float32)

            try:
                self._stream = sd.InputStream(
                    device=self._input_device,
                    channels=in_ch,
                    samplerate=in_sr,
                    dtype="float32",
                    callback=self._cb,
                    blocksize=512,
                )
                self._stream.start()
                self._running = True
                return True, ""
            except Exception as e:
                self._stream = None
                return False, str(e)

    def stop(self):
        with self._lock:
            if not self._running:
                return
            self._running = False
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

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

    def _cb(self, indata: np.ndarray, frames: int, time_info, status):
        if not self._running or self._mixer is None:
            return
        src_ch  = indata.shape[1]
        out_ch  = self._out_ch
        if src_ch == out_ch:
            chunk = indata.copy()
        elif src_ch < out_ch:
            chunk = np.tile(indata, (1, (out_ch // src_ch) + 1))[:, :out_ch]
        else:
            chunk = indata[:, :out_ch].copy()
        if self._in_sr != self._out_sr:
            ratio      = self._out_sr / self._in_sr
            out_len    = int(round(len(chunk) * ratio))
            old_x      = np.linspace(0, 1, len(chunk))
            new_x      = np.linspace(0, 1, out_len)
            resampled  = np.zeros((out_len, out_ch), dtype=np.float32)
            for c in range(out_ch):
                resampled[:, c] = np.interp(new_x, old_x, chunk[:, c])
            chunk = resampled
        vol = self._volume
        if vol > 0.0 and len(chunk) > 0:
            np.clip(chunk * vol, -1.0, 1.0, out=chunk)
            v = _Voice(chunk, 1.0, loop=False)
            self._mixer.add_voice(v)
