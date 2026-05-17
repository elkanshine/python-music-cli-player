"""
cmus-style Music Player — dengan fitur Playlist Management
pip install pygame rich syncedlyrics mutagen yt-dlp numpy pydub

Kontrol:
  Tab        = pindah panel (Library <-> Playlist)
  ↑ ↓        = navigasi
  Enter      = play / pilih / konfirmasi
  Space      = pause/resume
  n / p      = next / prev
  + / -      = volume naik / turun
  r          = toggle repeat (off -> all -> 1)
  /          = cari lagu lokal (real-time filter)
  d          = download dari YouTube (tampilkan hasil pencarian)
  k          = tampilkan lirik
  v          = audio visualizer
  a          = add lagu ke playlist kustom
  x          = hapus lagu dari playlist kustom
  N          = buat playlist baru
  D          = hapus playlist kustom
  1 2 3 4    = ganti tab
  Esc        = batal / kembali
  q          = keluar
"""

import os, re, sys, time, threading, json, msvcrt
from pathlib import Path

import pygame
from rich.console import Console
from rich.live    import Live
from rich.text    import Text
from rich.panel   import Panel
from rich.layout  import Layout
from rich.align   import Align
from rich         import box

try:
    import syncedlyrics; HAS_LYRICS = True
except ImportError:
    HAS_LYRICS = False

try:
    import mutagen.mp3; HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

try:
    import yt_dlp; HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

HAS_VIZ = HAS_NUMPY and HAS_PYDUB

# ── palette cmus ───────────────────────────────────────
C_GREEN   = "bright_green"
C_WHITE   = "white"
C_DIM     = "grey46"
C_YELLOW  = "yellow"
C_RED     = "bright_red"
C_CYAN    = "bright_cyan"
C_MAGENTA = "bright_magenta"
C_SEL_BG  = "on grey15"
C_TITLE   = "bold bright_green"
C_BORDER  = "grey30"

MUSIC_FOLDER    = Path("./music")
PLAYLIST_FILE   = Path("./playlists.json")
console         = Console()
pygame.mixer.init()

# ══════════════════════════════════════════════════════
#  CUSTOM PLAYLIST MANAGEMENT
# ══════════════════════════════════════════════════════
# Format: {"playlist_name": ["song_stem1", "song_stem2", ...]}
custom_playlists: dict[str, list[str]] = {}
pl_lock = threading.Lock()

def load_playlists():
    global custom_playlists
    if PLAYLIST_FILE.exists():
        try:
            with open(PLAYLIST_FILE, "r", encoding="utf-8") as f:
                custom_playlists = json.load(f)
        except Exception:
            custom_playlists = {}
    else:
        custom_playlists = {}

def save_playlists():
    with pl_lock:
        try:
            with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(custom_playlists, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

def create_playlist(name: str) -> bool:
    """Buat playlist baru. Return False jika sudah ada."""
    name = name.strip()
    if not name:
        return False
    with pl_lock:
        if name in custom_playlists:
            return False
        custom_playlists[name] = []
    save_playlists()
    return True

def delete_playlist(name: str) -> bool:
    """Hapus playlist kustom. Return False jika tidak ada."""
    with pl_lock:
        if name not in custom_playlists:
            return False
        del custom_playlists[name]
    save_playlists()
    return True

def add_song_to_playlist(pl_name: str, song_path: Path) -> bool:
    """Tambah lagu ke playlist. Return False jika sudah ada."""
    stem = song_path.stem
    with pl_lock:
        if pl_name not in custom_playlists:
            return False
        if stem in custom_playlists[pl_name]:
            return False
        custom_playlists[pl_name].append(stem)
    save_playlists()
    return True

def remove_song_from_playlist(pl_name: str, song_path: Path) -> bool:
    """Hapus lagu dari playlist. Return False jika tidak ada."""
    stem = song_path.stem
    with pl_lock:
        if pl_name not in custom_playlists:
            return False
        if stem not in custom_playlists[pl_name]:
            return False
        custom_playlists[pl_name].remove(stem)
    save_playlists()
    return True

def get_playlist_songs(pl_name: str) -> list[Path]:
    """Ambil daftar Path lagu dari playlist kustom."""
    with pl_lock:
        stems = list(custom_playlists.get(pl_name, []))
    result = []
    for stem in stems:
        p = MUSIC_FOLDER / f"{stem}.mp3"
        if p.exists():
            result.append(p)
    return result

def get_playlist_names() -> list[str]:
    with pl_lock:
        return list(custom_playlists.keys())

# ── state ──────────────────────────────────────────────
playlist      : list[Path]             = []
current_index : int                    = 0
paused        : bool                   = False
volume        : float                  = 0.8
lyrics_lines  : list[tuple[float,str]] = []
lyrics_lock   = threading.Lock()

REPEAT_OFF   = "off"
REPEAT_ALL   = "all"
REPEAT_TRACK = "track"
repeat_mode  : str = REPEAT_OFF

active_panel : str = "playlist"
lib_sel      : int = 0
pl_sel       : int = 0

# ── feedback message ────────────────────────────────────
feedback_msg  : str = ""
feedback_time : float = 0.0
FEEDBACK_DURATION = 2.5

def set_feedback(msg: str):
    global feedback_msg, feedback_time
    feedback_msg  = msg
    feedback_time = time.time()

def get_feedback() -> str:
    global feedback_msg
    if feedback_msg and (time.time() - feedback_time) > FEEDBACK_DURATION:
        feedback_msg = ""
    return feedback_msg

# ── download state ─────────────────────────────────────
dl_status    : str  = ""
dl_active    : bool = False
dl_lock      = threading.Lock()

# ── YouTube search results state ───────────────────────
yt_results   : list[dict] = []
yt_searching : bool = False
yt_lock      = threading.Lock()

# ── Audio visualizer state ─────────────────────────────
VIZ_BARS    = 48
VIZ_HEIGHT  = 16
VIZ_CHUNK   = 4096

viz_data    : list[float] = [0.0] * VIZ_BARS
viz_peaks   : list[float] = [0.0] * VIZ_BARS
viz_lock    = threading.Lock()
viz_running : bool = False
viz_stop_ev = threading.Event()
viz_thread  = None

_viz_cache_path : str = ""
_viz_samples    = None
_viz_sr         : int = 44100

# ── Library items (dinamis) ────────────────────────────
def build_library_items() -> list[tuple[str, str]]:
    items = [
        ("All Songs",      "library"),
        ("──────────",     "sep"),
        ("Search /",       "search"),
        ("Download d",     "download"),
        ("──────────",     "sep2"),
        ("Lyrics+Viz k/v", "karaoke"),
        ("──────────",     "sep3"),
        ("+ New Playlist N","new_playlist"),
    ]
    names = get_playlist_names()
    for name in names:
        cnt = len(custom_playlists.get(name, []))
        label = f"♫ {name[:16]} ({cnt})"
        items.append((label, f"pl:{name}"))
    return items

# ══════════════════════════════════════════════════════
#  INPUT
# ══════════════════════════════════════════════════════
KEY_UP    = "UP"
KEY_DOWN  = "DOWN"
KEY_LEFT  = "LEFT"
KEY_RIGHT = "RIGHT"
KEY_ENTER = "ENTER"
KEY_ESC   = "ESC"
KEY_SPACE = "SPACE"
KEY_TAB   = "TAB"

def read_key() -> str:
    ch = msvcrt.getch()
    if ch in (b'\r', b'\n'):  return KEY_ENTER
    if ch == b'\x1b':         return KEY_ESC
    if ch == b' ':            return KEY_SPACE
    if ch == b'\t':           return KEY_TAB
    if ch in (b'\xe0', b'\x00'):
        ch2 = msvcrt.getch()
        if ch2 == b'H': return KEY_UP
        if ch2 == b'P': return KEY_DOWN
        if ch2 == b'K': return KEY_LEFT
        if ch2 == b'M': return KEY_RIGHT
        return ""
    return ch.decode(errors="ignore").lower()

# ══════════════════════════════════════════════════════
#  LRC / LYRICS
# ══════════════════════════════════════════════════════
_LRC_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d+))?\](.*)")

def parse_lrc(raw: str) -> list:
    result = []
    for line in raw.splitlines():
        m = _LRC_RE.match(line.strip())
        if m:
            ts   = int(m.group(1))*60 + int(m.group(2)) + int(m.group(3) or 0)/100
            text = m.group(4).strip()
            if text: result.append((ts, text))
    return sorted(result, key=lambda x: x[0])

def _load_lyrics_thread(song_name: str):
    global lyrics_lines
    with lyrics_lock: lyrics_lines = []
    if not HAS_LYRICS: return
    try:
        raw = syncedlyrics.search(song_name)
        if raw and "[" in raw:
            parsed = parse_lrc(raw)
            if parsed:
                with lyrics_lock: lyrics_lines = parsed; return
    except Exception: pass
    try:
        raw = syncedlyrics.search(song_name, plain_only=True)
        if raw:
            with lyrics_lock:
                lyrics_lines = [(i*3.0, ln) for i,ln in enumerate(raw.splitlines()) if ln.strip()]
    except Exception: pass

def _try_local_lrc(p: Path):
    lrc = p.with_suffix(".lrc")
    if lrc.exists():
        try:
            parsed = parse_lrc(lrc.read_text(encoding="utf-8", errors="ignore"))
            if parsed: return parsed
        except Exception: pass
    return None

def current_lyric_idx() -> int:
    pos = pygame.mixer.music.get_pos() / 1000
    if pos < 0: return -1
    with lyrics_lock: lines = lyrics_lines
    idx = -1
    for i,(ts,_) in enumerate(lines):
        if ts <= pos: idx = i
        else: break
    return idx

# ══════════════════════════════════════════════════════
#  MUSIC CONTROLS
# ══════════════════════════════════════════════════════
def scan_music():
    global playlist
    MUSIC_FOLDER.mkdir(exist_ok=True)
    playlist = sorted(MUSIC_FOLDER.glob("*.mp3"))

def play_song(index: int):
    global current_index, paused, lyrics_lines
    if not (0 <= index < len(playlist)): return
    current_index = index; paused = False
    with lyrics_lock: lyrics_lines = []
    pygame.mixer.music.load(str(playlist[index]))
    pygame.mixer.music.set_volume(volume)
    pygame.mixer.music.play()
    local = _try_local_lrc(playlist[index])
    if local:
        with lyrics_lock: lyrics_lines = local
    elif HAS_LYRICS:
        threading.Thread(target=_load_lyrics_thread,
                         args=(playlist[index].stem,), daemon=True).start()

def toggle_pause():
    global paused
    if paused: pygame.mixer.music.unpause(); paused = False
    else:      pygame.mixer.music.pause();   paused = True

def stop_music():
    global lyrics_lines
    pygame.mixer.music.stop()
    with lyrics_lock: lyrics_lines = []

def toggle_repeat():
    global repeat_mode
    if   repeat_mode == REPEAT_OFF:   repeat_mode = REPEAT_ALL
    elif repeat_mode == REPEAT_ALL:   repeat_mode = REPEAT_TRACK
    else:                              repeat_mode = REPEAT_OFF

def next_song():
    global current_index
    if not playlist: return
    if repeat_mode == REPEAT_TRACK:
        play_song(current_index); return
    nxt = (current_index + 1) % len(playlist)
    if repeat_mode == REPEAT_OFF and nxt == 0 and current_index == len(playlist)-1:
        stop_music(); return
    current_index = nxt
    play_song(current_index)

def prev_song():
    global current_index
    if not playlist: return
    current_index = (current_index - 1) % len(playlist)
    play_song(current_index)

def change_volume(delta: float):
    global volume
    volume = max(0.0, min(1.0, volume + delta))
    pygame.mixer.music.set_volume(volume)

def get_duration(p: Path) -> str:
    if HAS_MUTAGEN:
        try:
            d = int(mutagen.mp3.MP3(str(p)).info.length)
            return f"{d//60}:{d%60:02d}"
        except: pass
    return "-:--"

def check_song_end():
    if not playlist or paused: return
    if not pygame.mixer.music.get_busy():
        next_song()

# ══════════════════════════════════════════════════════
#  DOWNLOAD + YOUTUBE SEARCH (yt-dlp)
# ══════════════════════════════════════════════════════
def _dl_progress_hook(d: dict):
    global dl_status
    status = d.get("status", "")
    if status == "downloading":
        pct   = d.get("_percent_str", "??%").strip()
        speed = d.get("_speed_str",   "?? ").strip()
        eta   = d.get("_eta_str",     "??").strip()
        fname = Path(d.get("filename", "")).stem[:35]
        with dl_lock:
            dl_status = f"{fname}  {pct}  {speed}  eta {eta}"
    elif status == "finished":
        with dl_lock:
            dl_status = "mengonversi ke mp3..."
    elif status == "error":
        with dl_lock:
            dl_status = "ERROR saat download"

def _yt_search_thread(query: str):
    global yt_results, yt_searching
    if not HAS_YTDLP:
        with yt_lock:
            yt_results = [{"title": "yt-dlp tidak terinstall (pip install yt-dlp)",
                           "duration": "", "url": "", "channel": ""}]
            yt_searching = False
        return

    ydl_opts = {
        "quiet":        True,
        "no_warnings":  True,
        "extract_flat": True,
        "noplaylist":   True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch7:{query}", download=False)
        entries = info.get("entries", []) if info else []
        results = []
        for e in entries:
            dur_s = e.get("duration") or 0
            m, s  = divmod(int(dur_s), 60)
            results.append({
                "title":   e.get("title",   "?")[:65],
                "channel": e.get("uploader", e.get("channel", "?"))[:25],
                "duration": f"{m}:{s:02d}" if dur_s else "-:--",
                "url":     e.get("url") or e.get("webpage_url", ""),
            })
        with yt_lock:
            yt_results   = results
            yt_searching = False
    except Exception as ex:
        with yt_lock:
            yt_results   = [{"title": f"Error: {str(ex)[:60]}", "duration": "",
                             "url": "", "channel": ""}]
            yt_searching = False

def start_yt_search(query: str):
    global yt_results, yt_searching
    with yt_lock:
        yt_results   = []
        yt_searching = True
    threading.Thread(target=_yt_search_thread, args=(query,), daemon=True).start()

def _download_url_thread(url: str, title: str):
    global dl_active, dl_status
    ydl_opts = {
        "format":         "bestaudio/best",
        "outtmpl":        str(MUSIC_FOLDER / "%(title)s.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3", "preferredquality": "192"}],
        "progress_hooks": [_dl_progress_hook],
        "quiet":          True,
        "no_warnings":    True,
        "noplaylist":     True,
    }
    try:
        with dl_lock: dl_status = f"memulai: {title[:40]}..."
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        with dl_lock: dl_status = f"selesai: {title[:40]}"
        time.sleep(2)
    except Exception as e:
        with dl_lock: dl_status = f"ERROR: {str(e)[:55]}"
        time.sleep(3)
    with dl_lock:
        dl_status = ""
        dl_active = False
    scan_music()

def start_download_url(url: str, title: str):
    global dl_active
    if dl_active: return
    dl_active = True
    threading.Thread(target=_download_url_thread, args=(url, title), daemon=True).start()

# ══════════════════════════════════════════════════════
#  AUDIO VISUALIZER
# ══════════════════════════════════════════════════════
def _load_audio_samples(path: Path):
    global _viz_cache_path, _viz_samples, _viz_sr
    p = str(path)
    if p == _viz_cache_path and _viz_samples is not None:
        return _viz_samples, _viz_sr
    try:
        seg = AudioSegment.from_file(p)
        seg = seg.set_channels(1).set_frame_rate(44100)
        raw = np.array(seg.get_array_of_samples(), dtype=np.float32)
        mx  = np.abs(raw).max()
        if mx > 0: raw /= mx
        _viz_cache_path = p
        _viz_samples    = raw
        _viz_sr         = 44100
        return raw, 44100
    except Exception:
        _viz_cache_path = ""
        _viz_samples    = None
        return None, 44100

def _fft_frame(samples, sr: int, pos_sec: float) -> list:
    start = int(pos_sec * sr)
    chunk = samples[start : start + VIZ_CHUNK]
    if len(chunk) < VIZ_CHUNK:
        chunk = np.pad(chunk, (0, VIZ_CHUNK - len(chunk)))
    win     = np.hanning(len(chunk))
    fft_out = np.abs(np.fft.rfft(chunk * win))
    n_fft   = len(fft_out)
    lo_bin = max(1, int(20    / (sr / 2) * n_fft))
    hi_bin = min(n_fft - 1, int(16000 / (sr / 2) * n_fft))
    bins   = np.logspace(np.log10(lo_bin), np.log10(hi_bin), VIZ_BARS + 1, dtype=int)
    amps   = np.zeros(VIZ_BARS)
    for i in range(VIZ_BARS):
        b0, b1 = bins[i], max(bins[i] + 1, bins[i + 1])
        amps[i] = fft_out[b0 : min(b1, n_fft)].mean()
    amps = np.log1p(amps * 10)
    mx   = amps.max()
    if mx > 0: amps /= mx
    return amps.tolist()

def _viz_worker():
    global viz_running
    prev_path = ""
    samples   = None
    sr        = 44100
    while not viz_stop_ev.is_set():
        if not playlist or paused:
            time.sleep(0.04); continue
        cur_path = str(playlist[current_index])
        if cur_path != prev_path:
            samples, sr = _load_audio_samples(playlist[current_index])
            prev_path   = cur_path
        if samples is None:
            time.sleep(0.04); continue
        pos_ms = pygame.mixer.music.get_pos()
        if pos_ms < 0:
            time.sleep(0.04); continue
        pos_sec  = pos_ms / 1000.0
        new_amps = _fft_frame(samples, sr, pos_sec)
        with viz_lock:
            for i in range(VIZ_BARS):
                a = new_amps[i]
                if a > viz_data[i]:
                    viz_data[i] = a * 0.65 + viz_data[i] * 0.35
                else:
                    viz_data[i] = viz_data[i] * 0.82
                if a >= viz_peaks[i]:
                    viz_peaks[i] = a
                else:
                    viz_peaks[i] = max(0.0, viz_peaks[i] - 0.022)
        time.sleep(0.04)

def start_visualizer():
    global viz_running, viz_thread
    if not HAS_VIZ: return
    if viz_running: return
    viz_stop_ev.clear()
    with viz_lock:
        viz_data[:]  = [0.0] * VIZ_BARS
        viz_peaks[:] = [0.0] * VIZ_BARS
    viz_thread = threading.Thread(target=_viz_worker, daemon=True)
    viz_thread.start()
    viz_running = True

def stop_visualizer():
    global viz_running, viz_thread
    viz_stop_ev.set()
    viz_running = False
    viz_thread  = None

# ══════════════════════════════════════════════════════
#  RENDER COMPONENTS
# ══════════════════════════════════════════════════════
def fmt_time(sec: float) -> str:
    m, s = divmod(int(max(sec, 0)), 60)
    return f"{m}:{s:02d}"

def progress_bar(width: int = 40) -> Text:
    t = Text(no_wrap=True)
    if not playlist:
        t.append("─" * width, style=C_DIM)
        return t
    pos = max(pygame.mixer.music.get_pos()/1000, 0)
    dur = 0
    if HAS_MUTAGEN:
        try: dur = mutagen.mp3.MP3(str(playlist[current_index])).info.length
        except: pass
    frac  = (pos / dur) if dur > 0 else 0
    done  = round(frac * width)
    empty = width - done
    if done > 0:
        t.append("━" * done, style=C_GREEN)
    if empty > 0:
        t.append("╸", style=C_GREEN)
        t.append("─" * max(0, empty - 1), style=C_DIM)
    return t

def vol_bar(width: int = 8) -> Text:
    filled = round(volume * width)
    t = Text(no_wrap=True)
    t.append("█" * filled,          style=C_GREEN)
    t.append("░" * (width - filled), style=C_DIM)
    return t

def make_tab_bar(view: str) -> Text:
    tabs = [
        ("1:library",   "library"),
        ("2:playlist",  "playlist"),
        ("3:lyrics+viz","karaoke"),
    ]
    t = Text()
    for label, key in tabs:
        active = (view == key) or (view == "viz" and key == "karaoke") or \
                 (view.startswith("pl:") and key == "playlist")
        if active:
            t.append(f" {label} ", style=f"bold {C_GREEN} on grey19")
        else:
            t.append(f" {label} ", style=C_DIM)
        t.append(" ")
    return t

# ── Status bar ─────────────────────────────────────────
def make_status_bar(mode: str = "normal", input_text: str = "",
                    ctx_pl: str = "") -> Text:
    if mode == "search_input":
        t = Text()
        t.append(" / ", style=f"bold {C_GREEN}")
        t.append("cari lagu: ", style=C_DIM)
        t.append(input_text, style=C_WHITE)
        t.append("█", style=C_GREEN)
        return t

    if mode == "dl_input":
        t = Text()
        t.append(" d ", style=f"bold {C_YELLOW}")
        t.append("cari YouTube: ", style=C_DIM)
        t.append(input_text, style=C_WHITE)
        t.append("█", style=C_GREEN)
        return t

    if mode == "dl_results":
        t = Text()
        t.append(" ↑↓ ", style=f"bold {C_GREEN}")
        t.append("pilih lagu  ", style=C_DIM)
        t.append("Enter", style=f"bold {C_GREEN}")
        t.append(":download  ", style=C_DIM)
        t.append("Esc", style=f"bold {C_GREEN}")
        t.append(":batal", style=C_DIM)
        return t

    if mode == "new_playlist":
        t = Text()
        t.append(" N ", style=f"bold {C_CYAN}")
        t.append("nama playlist baru: ", style=C_DIM)
        t.append(input_text, style=C_WHITE)
        t.append("█", style=C_CYAN)
        return t

    if mode == "add_to_playlist":
        t = Text()
        t.append(" a ", style=f"bold {C_MAGENTA}")
        t.append("pilih playlist → ", style=C_DIM)
        t.append(input_text or "(tekan ↑↓ lalu Enter)", style=C_WHITE)
        return t

    if mode == "confirm_delete_pl":
        t = Text()
        t.append(" D ", style=f"bold {C_RED}")
        t.append(f"hapus playlist '{ctx_pl}'? ", style=C_DIM)
        t.append("Enter", style=f"bold {C_RED}")
        t.append(":ya  ", style=C_DIM)
        t.append("Esc", style=f"bold {C_GREEN}")
        t.append(":batal", style=C_DIM)
        return t

    # feedback message (temporary)
    fb = get_feedback()
    if fb:
        t = Text(no_wrap=True)
        t.append(f" ✓ {fb}", style=f"bold {C_CYAN}")
        return t

    # download progress
    with dl_lock:
        dls      = dl_status
        dlactive = dl_active
    if dlactive or dls:
        t = Text(no_wrap=True)
        t.append(" ↓ ", style=f"bold {C_YELLOW}")
        t.append(dls, style=C_YELLOW)
        return t

    # normal playback
    t = Text(no_wrap=True)
    if not playlist:
        t.append(" ♪  belum ada lagu — tekan d untuk download dari YouTube", style=C_DIM)
        return t
    song  = playlist[current_index].stem
    pos_s = max(pygame.mixer.music.get_pos()/1000, 0)
    dur   = 0
    if HAS_MUTAGEN:
        try: dur = mutagen.mp3.MP3(str(playlist[current_index])).info.length
        except: pass
    icon       = "|| " if paused else ">> "
    icon_style = C_YELLOW if paused else C_GREEN
    t.append(f" {icon}", style=f"bold {icon_style}")
    t.append(song[:50], style=f"bold {C_WHITE}")
    t.append(f"  {fmt_time(pos_s)}", style=C_GREEN)
    if dur: t.append(f"/{fmt_time(dur)}", style=C_DIM)
    t.append(f"  vol:{int(volume*100)}%", style=C_DIM)
    t.append("  ")
    if   repeat_mode == REPEAT_ALL:   t.append("repeat:all", style=C_GREEN)
    elif repeat_mode == REPEAT_TRACK: t.append("repeat:1",   style=C_YELLOW)
    else:                             t.append("repeat:off", style=C_DIM)
    return t

# ── Footer ─────────────────────────────────────────────
FOOTER_NORMAL = (
    "Tab:panel", "↑↓:nav", "Enter:play", "spc:pause",
    "n/p:skip",  "+/-:vol", "r:repeat",
    "/:cari",    "d:yt-dl", "k/v:viz", "a:add-pl", "x:rm-pl", "N:new-pl", "D:del-pl", "q:quit",
)
FOOTER_SEARCH = (
    "ketik:filter real-time", "Enter:konfirmasi", "Esc:batal",
)
FOOTER_DL_INPUT = (
    "ketik:judul/URL YouTube", "Enter:cari hasil", "Esc:batal",
)
FOOTER_DL_RESULTS = (
    "↑↓:pilih", "Enter:download", "d:cari lagi", "Esc:batal",
)
FOOTER_NEW_PL = (
    "ketik:nama playlist", "Enter:buat", "Esc:batal",
)
FOOTER_ADD_TO_PL = (
    "↑↓:pilih playlist", "Enter:tambah", "Esc:batal",
)
FOOTER_CONFIRM_DEL = (
    "Enter:hapus playlist", "Esc:batal",
)

def make_footer(hints: tuple = FOOTER_NORMAL) -> Text:
    t = Text(no_wrap=True)
    t.append(" ")
    for i, hint in enumerate(hints):
        key, _, desc = hint.partition(":")
        t.append(key, style=f"bold {C_GREEN}")
        t.append(f":{desc}", style=C_DIM)
        if i < len(hints) - 1:
            t.append("  ")
    return t

# ── Library pane ───────────────────────────────────────
def make_library_panel(focused: bool, current_view: str = "") -> Panel:
    items = build_library_items()
    t = Text()
    for i, (label, key) in enumerate(items):
        if key.startswith("sep"):
            t.append(f"  {'─'*14}\n", style=C_DIM)
            continue
        is_sel     = (i == lib_sel)
        is_active  = (key == current_view) or \
                     (current_view.startswith("pl:") and key == current_view)
        if is_sel and focused:
            t.append(f" ▶ {label}\n", style=f"bold {C_GREEN} {C_SEL_BG}")
        elif is_sel:
            t.append(f" ▶ {label}\n", style=f"{C_WHITE} {C_SEL_BG}")
        elif is_active:
            t.append(f" ► {label}\n", style=f"bold {C_CYAN}")
        else:
            t.append(f"   {label}\n", style=C_DIM)
    border = C_GREEN if focused else C_BORDER
    title  = f"[{C_TITLE}]Library[/{C_TITLE}]" if focused else f"[{C_DIM}]Library[/{C_DIM}]"
    return Panel(t, title=title, border_style=border, box=box.SIMPLE_HEAVY, padding=(0,0))

# ── Playlist pane ──────────────────────────────────────
def make_playlist_panel(src: list[Path], title: str,
                         focused: bool, sel: int,
                         height: int = 20,
                         is_custom: bool = False,
                         custom_pl_name: str = "") -> Panel:
    t = Text()
    if not src:
        t.append("\n  (kosong", style=C_DIM)
        if is_custom:
            t.append(" — tekan a untuk tambah lagu", style=C_DIM)
        else:
            t.append(" — taruh .mp3 di folder ./music atau tekan d untuk download", style=C_DIM)
        t.append(")\n", style=C_DIM)
    else:
        PAGE       = max(height - 2, 4)
        page_start = (sel // PAGE) * PAGE
        page_end   = min(page_start + PAGE, len(src))
        for i in range(page_start, page_end):
            song   = src[i]
            dur    = get_duration(song)
            lrc    = "♪" if song.with_suffix(".lrc").exists() else " "
            is_cur = bool(playlist) and (song == playlist[current_index])
            arrow  = "▶" if is_cur else " "
            name   = song.stem
            if len(name) > 44: name = name[:42] + ".."
            # Show playlist indicator if in a custom playlist
            in_pl_names = [pn for pn, stems in custom_playlists.items() if song.stem in stems]
            pl_tag = f"[{','.join(in_pl_names[:2])}]" if in_pl_names and not is_custom else ""
            if len(pl_tag) > 14: pl_tag = pl_tag[:12] + "..]"
            line = f" {arrow} {i+1:>3}  {name:<44}  {dur:>5}  {lrc}"
            if pl_tag and not is_custom:
                line += f"  {pl_tag[:14]}"
            line += "\n"
            if i == sel and focused:     style = f"bold {C_GREEN} {C_SEL_BG}"
            elif i == sel:               style = f"{C_WHITE} {C_SEL_BG}"
            elif is_cur:                 style = f"bold {C_GREEN}"
            else:                        style = C_DIM
            t.append(line, style=style)

    border  = C_GREEN if focused else C_BORDER
    cnt     = len(src) if src else 0

    if is_custom:
        title_text = (f"[{C_TITLE}]♫ {title}[/{C_TITLE}] [{C_DIM}]({cnt} lagu)[/{C_DIM}]"
                      + f" [{C_DIM}]| a:add  x:del  D:hapus-playlist[/{C_DIM}]")
    else:
        title_text = (f"[{C_TITLE}]{title} ({cnt})[/{C_TITLE}]" if focused
                      else f"[{C_DIM}]{title} ({cnt})[/{C_DIM}]")

    return Panel(t, title=title_text, border_style=border, box=box.SIMPLE_HEAVY, padding=(0,0))

# ── Add-to-playlist selection overlay ─────────────────
def make_add_to_pl_panel(pl_names: list[str], sel_idx: int, song_name: str) -> Panel:
    t = Text()
    t.append(f"\n  Lagu: ", style=C_DIM)
    t.append(f"{song_name[:50]}\n\n", style=f"bold {C_WHITE}")
    if not pl_names:
        t.append("  Belum ada playlist — tekan N untuk buat playlist baru\n", style=C_DIM)
    else:
        for i, name in enumerate(pl_names):
            cnt = len(custom_playlists.get(name, []))
            already = song_name in custom_playlists.get(name, [])
            mark = " ✓" if already else "  "
            if i == sel_idx:
                t.append(f"  ▶ {name:<30} ({cnt} lagu){mark}\n",
                         style=f"bold {C_MAGENTA} {C_SEL_BG}")
            else:
                t.append(f"    {name:<30} ({cnt} lagu){mark}\n", style=C_DIM)
    return Panel(t,
                 title=f"[bold {C_MAGENTA}]Tambah ke Playlist[/bold {C_MAGENTA}]",
                 border_style=C_MAGENTA, box=box.SIMPLE_HEAVY)

# ── YouTube search results pane ────────────────────────
def make_yt_panel(results: list[dict], sel: int, searching: bool) -> Panel:
    t = Text()
    if searching:
        t.append("\n  mencari di YouTube...\n", style=C_YELLOW)
    elif not results:
        t.append("\n  ketik judul lagu lalu Enter untuk mencari\n", style=C_DIM)
    else:
        t.append("\n")
        for i, r in enumerate(results):
            title   = r["title"]
            dur     = r["duration"]
            channel = r["channel"]
            num     = f"{i+1:>2}."
            line_top    = f"  {num} {title}\n"
            line_bottom = f"       {channel}  {dur}\n"
            if i == sel:
                t.append(line_top,    style=f"bold {C_GREEN} {C_SEL_BG}")
                t.append(line_bottom, style=f"{C_DIM} {C_SEL_BG}")
            else:
                t.append(line_top,    style=C_WHITE)
                t.append(line_bottom, style=C_DIM)

    with dl_lock:
        dls = dl_status; dlact = dl_active
    if dlact or dls:
        t.append(f"\n  ↓ {dls}\n", style=C_YELLOW)

    return Panel(t,
                 title=f"[{C_TITLE}]YouTube — pilih lagu untuk download[/{C_TITLE}]",
                 border_style=C_YELLOW, box=box.SIMPLE_HEAVY, padding=(0,0))

# ── Lyrics pane ────────────────────────────────────────
def _render_lyrics_rows(height: int) -> Text:
    with lyrics_lock: lines = list(lyrics_lines)
    t = Text(justify="center", no_wrap=True)
    if not lines:
        if HAS_LYRICS:
            t.append("  ♪  memuat lirik...\n", style=C_DIM)
        else:
            t.append("  ♪  (pip install syncedlyrics)\n", style=C_DIM)
        return t
    idx   = current_lyric_idx()
    if idx < 0: idx = 0
    half  = max(1, (height - 1) // 2)
    start = max(0, idx - half)
    end   = min(len(lines), start + height)
    if end - start < height:
        start = max(0, end - height)
    for i in range(start, end):
        _, text = lines[i]
        dist = abs(i - idx)
        txt  = text[:80]
        if i == idx:    t.append(f"  ♫  {txt}  ♫\n", style=f"bold {C_WHITE} on grey19")
        elif dist == 1: t.append(f"     {txt}\n",     style=C_WHITE)
        elif dist == 2: t.append(f"     {txt}\n",     style=C_GREEN)
        else:           t.append(f"     {txt}\n",     style=C_DIM)
    return t

_BLOCKS = " ▁▂▃▄▅▆▇█"

def _bar_color(bar_idx: int, total: int) -> str:
    ratio = bar_idx / max(total - 1, 1)
    if ratio < 0.35:   return C_GREEN
    elif ratio < 0.65: return "bright_cyan"
    else:              return C_YELLOW

def _amp_to_blocks(amp: float, peak: float, height: int) -> list[str]:
    filled_f = amp * height
    filled_i = int(filled_f)
    frac     = filled_f - filled_i
    col      = []
    for row in range(height):
        if row < filled_i:
            col.append("█")
        elif row == filled_i:
            idx = min(8, max(0, round(frac * 8)))
            col.append(_BLOCKS[idx])
        else:
            col.append(" ")
    peak_row = min(height - 1, int(peak * height))
    if peak_row > filled_i and col[peak_row] == " ":
        col[peak_row] = "─"
    return col

def _render_viz_rows(panel_w: int, bar_h: int) -> Text:
    with viz_lock:
        amps  = list(viz_data)
        peaks = list(viz_peaks)
    bar_w = 2
    n     = min(VIZ_BARS, panel_w // bar_w)
    t = Text(no_wrap=True)
    if not HAS_VIZ or not viz_running:
        for _ in range(bar_h):
            t.append("\n")
        return t
    cols_data = []
    for i in range(n):
        src_i = int(i * VIZ_BARS / n)
        amp   = min(0.95, amps[src_i])
        pk    = min(0.99, peaks[src_i])
        cols_data.append(_amp_to_blocks(amp, pk, bar_h))
    for row in range(bar_h - 1, -1, -1):
        t.append(" ")
        for i, col in enumerate(cols_data):
            ch  = col[row]
            clr = _bar_color(i, n)
            if ch == "─":
                t.append(ch + " ", style=C_WHITE)
            elif ch != " ":
                t.append(ch + " ", style=f"bold {clr}")
            else:
                t.append("  ")
        t.append("\n")
    freq_map = [("20Hz",0.00),("250Hz",0.10),("1kHz",0.35),("4kHz",0.65),("8kHz",0.92)]
    label_line = [" "] * (n * bar_w + 1)
    for lbl, ratio in freq_map:
        pos = 1 + int(ratio * n) * bar_w
        for j, ch in enumerate(lbl):
            if pos + j < len(label_line):
                label_line[pos + j] = ch
    t.append("".join(label_line), style=C_DIM)
    t.append("\n")
    return t

def make_lyric_viz_panel(total_height: int) -> Panel:
    song_name = playlist[current_index].stem if playlist else ""
    title_str = (f"[{C_TITLE}]Lyrics + Viz[/{C_TITLE}]"
                 + (f"  [{C_DIM}]{song_name[:45]}[/{C_DIM}]" if song_name else ""))
    inner_h  = max(6, total_height - 2)
    viz_h    = max(3, int(inner_h * 0.55))
    lyr_h    = max(2, inner_h - viz_h - 1)
    term_w  = console.width or 80
    panel_w = max(20, term_w - 24)
    t = Text(no_wrap=True)
    if HAS_VIZ and viz_running:
        t.append_text(_render_viz_rows(panel_w, viz_h))
    else:
        hint = "tekan v untuk aktifkan visualizer" if HAS_VIZ else "pip install numpy pydub"
        for _ in range(viz_h // 2):
            t.append("\n")
        t.append(f"  {hint}\n", style=C_DIM)
        for _ in range(viz_h - viz_h // 2 - 1):
            t.append("\n")
    sep_w = min(panel_w, term_w - 4)
    t.append(" " + "─" * sep_w + "\n", style=C_DIM)
    t.append_text(_render_lyrics_rows(lyr_h))
    return Panel(t, title=title_str, border_style=C_GREEN, box=box.SIMPLE_HEAVY)

# ── Full layout ────────────────────────────────────────
def build_layout(view: str,
                 pl_src: list[Path], pl_title: str, pl_sel_idx: int,
                 mode: str, input_text: str,
                 yt_res: list[dict], yt_sel: int, yt_srch: bool,
                 height: int,
                 is_custom_pl: bool = False,
                 custom_pl_name: str = "",
                 add_pl_names: list[str] = None,
                 add_pl_sel: int = 0,
                 ctx_pl: str = "") -> Layout:
    root = Layout()
    root.split_column(
        Layout(name="tabs",    size=1),
        Layout(name="body"),
        Layout(name="progbar", size=1),
        Layout(name="status",  size=1),
        Layout(name="footer",  size=1),
    )

    root["tabs"].update(make_tab_bar(view))

    pb = Text(no_wrap=True)
    pb.append("  ")
    pb_width = max(20, (console.width or 80) - 22)
    pb.append_text(progress_bar(pb_width))
    pb.append("  ")
    pb.append_text(vol_bar())
    root["progbar"].update(pb)

    body   = Layout()
    body_h = max(height - 5, 4)
    lib_w  = 22
    body.split_row(
        Layout(name="lib",  size=lib_w),
        Layout(name="main"),
    )
    body["lib"].update(make_library_panel(focused=(active_panel == "library"),
                                           current_view=view))

    if mode == "add_to_playlist" and add_pl_names is not None:
        song_name = pl_src[pl_sel_idx].stem if pl_src and 0 <= pl_sel_idx < len(pl_src) else ""
        body["main"].update(make_add_to_pl_panel(add_pl_names, add_pl_sel, song_name))
    elif mode in ("dl_input", "dl_results"):
        body["main"].update(make_yt_panel(yt_res, yt_sel, yt_srch))
    elif view in ("karaoke", "viz"):
        body["main"].update(make_lyric_viz_panel(total_height=body_h))
    else:
        body["main"].update(
            make_playlist_panel(pl_src, pl_title,
                                focused=(active_panel == "playlist"),
                                sel=pl_sel_idx, height=body_h,
                                is_custom=is_custom_pl,
                                custom_pl_name=custom_pl_name)
        )

    root["body"].update(body)
    root["status"].update(make_status_bar(mode, input_text, ctx_pl))

    hint_map = {
        "search_input":      FOOTER_SEARCH,
        "dl_input":          FOOTER_DL_INPUT,
        "dl_results":        FOOTER_DL_RESULTS,
        "new_playlist":      FOOTER_NEW_PL,
        "add_to_playlist":   FOOTER_ADD_TO_PL,
        "confirm_delete_pl": FOOTER_CONFIRM_DEL,
    }
    root["footer"].update(make_footer(hint_map.get(mode, FOOTER_NORMAL)))
    return root

# ══════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════
def main_loop():
    global active_panel, lib_sel, pl_sel

    view      = "library"
    mode      = "normal"
    input_txt = ""

    pl_src        : list[Path] = list(playlist)
    pl_title       = "All Songs"
    pl_sel_v       = 0
    is_custom_view = False
    current_cpl    = ""      # nama custom playlist yang sedang ditampilkan

    yt_sel    = 0

    # add-to-playlist overlay state
    add_pl_names : list[str] = []
    add_pl_sel   : int = 0

    # confirm-delete playlist state
    del_pl_target : str = ""

    try:    rows = os.get_terminal_size().lines
    except: rows = 30

    def _get_yt():
        with yt_lock: return list(yt_results), yt_searching

    def _refresh_pl_src():
        """Refresh pl_src dari sumber yang sedang aktif."""
        nonlocal pl_src, pl_title, is_custom_view
        if is_custom_view and current_cpl:
            pl_src  = get_playlist_songs(current_cpl)
            pl_title = current_cpl
        else:
            pl_src  = list(playlist)
            pl_title = "All Songs"

    with Live(console=console, refresh_per_second=20, screen=True) as live:
        while True:
            check_song_end()
            try:    rows = os.get_terminal_size().lines
            except: pass

            res, srch = _get_yt()
            live.update(build_layout(
                view, pl_src, pl_title, pl_sel_v,
                mode, input_txt, res, yt_sel, srch, rows,
                is_custom_view, current_cpl,
                add_pl_names if mode == "add_to_playlist" else None,
                add_pl_sel,
                ctx_pl=del_pl_target if mode == "confirm_delete_pl" else ""
            ))

            if not msvcrt.kbhit():
                time.sleep(0.04 if view in ("viz","karaoke") else 0.06)
                continue

            k = read_key()

            # ══════════════════════════════════════════
            #  MODE: dl_input
            # ══════════════════════════════════════════
            if mode == "dl_input":
                if k == KEY_ESC:
                    mode = "normal"; input_txt = ""
                elif k == KEY_ENTER:
                    if input_txt.strip():
                        start_yt_search(input_txt.strip())
                        mode = "dl_results"; yt_sel = 0
                    else:
                        mode = "normal"; input_txt = ""
                elif k in ("\x08", "backspace"):
                    input_txt = input_txt[:-1]
                elif len(k) == 1 and k.isprintable():
                    input_txt += k
                continue

            # ══════════════════════════════════════════
            #  MODE: dl_results
            # ══════════════════════════════════════════
            if mode == "dl_results":
                res, srch = _get_yt()
                if k == KEY_ESC:
                    mode = "normal"; input_txt = ""
                    with yt_lock: yt_results.clear()
                elif k == "d":
                    mode = "dl_input"; input_txt = ""
                    with yt_lock: yt_results.clear()
                elif k == KEY_UP and res:
                    yt_sel = max(0, yt_sel - 1)
                elif k == KEY_DOWN and res:
                    yt_sel = min(len(res) - 1, yt_sel + 1)
                elif k == KEY_ENTER and res and not srch:
                    chosen = res[yt_sel]
                    if chosen["url"] and not dl_active:
                        start_download_url(chosen["url"], chosen["title"])
                        mode = "normal"; input_txt = ""
                        with yt_lock: yt_results.clear()
                continue

            # ══════════════════════════════════════════
            #  MODE: search_input
            # ══════════════════════════════════════════
            if mode == "search_input":
                if k == KEY_ESC:
                    mode = "normal"; input_txt = ""
                    _refresh_pl_src(); pl_sel_v = 0
                elif k == KEY_ENTER:
                    mode = "normal"; active_panel = "playlist"
                    pl_sel = pl_sel_v
                elif k in ("\x08", "backspace"):
                    input_txt = input_txt[:-1]
                    if is_custom_view and current_cpl:
                        base = get_playlist_songs(current_cpl)
                    else:
                        base = list(playlist)
                    pl_src = ([p for p in base if input_txt.lower() in p.stem.lower()]
                              if input_txt else base)
                    pl_title  = f"/{input_txt}" if input_txt else ("All Songs" if not is_custom_view else current_cpl)
                    pl_sel_v  = 0
                elif len(k) == 1 and k.isprintable():
                    input_txt += k
                    if is_custom_view and current_cpl:
                        base = get_playlist_songs(current_cpl)
                    else:
                        base = list(playlist)
                    pl_src    = [p for p in base if input_txt.lower() in p.stem.lower()]
                    pl_title  = f"/{input_txt}"
                    pl_sel_v  = 0
                elif k == KEY_UP:
                    pl_sel_v = max(0, pl_sel_v - 1)
                elif k == KEY_DOWN:
                    pl_sel_v = min(len(pl_src) - 1, pl_sel_v + 1) if pl_src else 0
                elif k == KEY_ENTER and pl_src:
                    try:
                        real_idx = playlist.index(pl_src[pl_sel_v])
                        play_song(real_idx); mode = "normal"
                    except ValueError: pass
                continue

            # ══════════════════════════════════════════
            #  MODE: new_playlist — ketik nama playlist baru
            # ══════════════════════════════════════════
            if mode == "new_playlist":
                if k == KEY_ESC:
                    mode = "normal"; input_txt = ""
                elif k == KEY_ENTER:
                    if input_txt.strip():
                        ok = create_playlist(input_txt.strip())
                        if ok:
                            set_feedback(f"Playlist '{input_txt.strip()}' dibuat!")
                        else:
                            set_feedback(f"Playlist '{input_txt.strip()}' sudah ada!")
                    mode = "normal"; input_txt = ""
                elif k in ("\x08", "backspace"):
                    input_txt = input_txt[:-1]
                elif len(k) == 1 and k.isprintable():
                    input_txt += k
                continue

            # ══════════════════════════════════════════
            #  MODE: add_to_playlist — pilih playlist tujuan
            # ══════════════════════════════════════════
            if mode == "add_to_playlist":
                if k == KEY_ESC:
                    mode = "normal"; input_txt = ""
                elif k == KEY_UP:
                    add_pl_sel = max(0, add_pl_sel - 1)
                elif k == KEY_DOWN:
                    add_pl_sel = min(len(add_pl_names) - 1, add_pl_sel + 1) if add_pl_names else 0
                elif k == KEY_ENTER and add_pl_names:
                    target_pl  = add_pl_names[add_pl_sel]
                    if pl_src and 0 <= pl_sel_v < len(pl_src):
                        ok = add_song_to_playlist(target_pl, pl_src[pl_sel_v])
                        if ok:
                            set_feedback(f"Ditambahkan ke '{target_pl}'!")
                        else:
                            set_feedback(f"Sudah ada di '{target_pl}'")
                    mode = "normal"; input_txt = ""
                    _refresh_pl_src()
                continue

            # ══════════════════════════════════════════
            #  MODE: confirm_delete_pl
            # ══════════════════════════════════════════
            if mode == "confirm_delete_pl":
                if k == KEY_ESC:
                    mode = "normal"; del_pl_target = ""
                elif k == KEY_ENTER and del_pl_target:
                    delete_playlist(del_pl_target)
                    set_feedback(f"Playlist '{del_pl_target}' dihapus!")
                    # kembali ke All Songs jika yang dihapus sedang ditampilkan
                    nonlocal_reset = (current_cpl == del_pl_target)
                    del_pl_target = ""
                    mode = "normal"
                    if nonlocal_reset:
                        is_custom_view = False
                        current_cpl = ""
                        view = "library"
                        pl_src   = list(playlist)
                        pl_title = "All Songs"
                        pl_sel_v = 0
                continue

            # ══════════════════════════════════════════
            #  MODE: normal — hotkey global
            # ══════════════════════════════════════════
            if   k in ("q",):     stop_music(); break
            elif k == KEY_ESC:    stop_music(); break
            elif k == KEY_SPACE:  toggle_pause()
            elif k == "n":        next_song()
            elif k == "p":        prev_song()
            elif k in ("+", "="): change_volume(0.05)
            elif k == "-":        change_volume(-0.05)
            elif k == "r":
                toggle_repeat()
                _refresh_pl_src()
            elif k == "/":
                mode = "search_input"; input_txt = ""
                _refresh_pl_src()
                pl_sel_v = pl_sel; active_panel = "playlist"
            elif k == "d":
                if not dl_active:
                    mode = "dl_input"; input_txt = ""
                    with yt_lock: yt_results.clear()

            # ── Playlist management hotkeys ──
            elif k == "n":  # buat playlist baru (shift-N → 'n' di read_key)
                pass  # ditangani di bawah dengan "N" lowercase
            elif k == "N" or k == "\x0e":  # N atau Ctrl+N
                mode = "new_playlist"; input_txt = ""

            elif k == "a":
                # Add lagu yang dipilih ke custom playlist
                if pl_src and 0 <= pl_sel_v < len(pl_src):
                    add_pl_names = get_playlist_names()
                    add_pl_sel   = 0
                    mode = "add_to_playlist"; input_txt = ""
                else:
                    set_feedback("Pilih lagu terlebih dahulu")

            elif k == "x":
                # Hapus lagu dari custom playlist (hanya jika sedang di custom playlist)
                if is_custom_view and current_cpl and pl_src and 0 <= pl_sel_v < len(pl_src):
                    song = pl_src[pl_sel_v]
                    ok = remove_song_from_playlist(current_cpl, song)
                    if ok:
                        set_feedback(f"'{song.stem[:30]}' dihapus dari '{current_cpl}'")
                    _refresh_pl_src()
                    pl_sel_v = min(pl_sel_v, max(0, len(pl_src) - 1))
                else:
                    set_feedback("x: hanya di view custom playlist")

            elif k == "D" or k == "\x04":  # D atau Ctrl+D
                # Hapus custom playlist yang sedang dilihat
                if is_custom_view and current_cpl:
                    del_pl_target = current_cpl
                    mode = "confirm_delete_pl"
                else:
                    set_feedback("Buka custom playlist dulu (klik di Library)")

            elif k == "1":    view = "library"; is_custom_view = False; current_cpl = ""
            elif k == "2":    view = "library"; active_panel = "playlist"
            elif k == "3":    view = "karaoke"; start_visualizer()
            elif k in ("k", "v"):
                if view == "karaoke":
                    if viz_running: stop_visualizer()
                    else:           start_visualizer()
                else:
                    view = "karaoke"; start_visualizer()

            elif k == KEY_TAB:
                active_panel = "playlist" if active_panel == "library" else "library"

            elif k == KEY_UP:
                if active_panel == "library":
                    items = build_library_items()
                    lib_sel = (lib_sel - 1) % len(items)
                    while items[lib_sel][1].startswith("sep"):
                        lib_sel = (lib_sel - 1) % len(items)
                elif pl_src:
                    pl_sel_v = max(0, pl_sel_v - 1)
                    pl_sel   = pl_sel_v

            elif k == KEY_DOWN:
                if active_panel == "library":
                    items = build_library_items()
                    lib_sel = (lib_sel + 1) % len(items)
                    while items[lib_sel][1].startswith("sep"):
                        lib_sel = (lib_sel + 1) % len(items)
                elif pl_src:
                    pl_sel_v = min(len(pl_src) - 1, pl_sel_v + 1)
                    pl_sel   = pl_sel_v

            elif k == KEY_ENTER:
                if active_panel == "library":
                    items  = build_library_items()
                    action = items[lib_sel][1]

                    if action == "search":
                        mode = "search_input"; input_txt = ""
                        _refresh_pl_src()
                        pl_sel_v = pl_sel; active_panel = "playlist"

                    elif action == "download":
                        if not dl_active:
                            mode = "dl_input"; input_txt = ""
                            with yt_lock: yt_results.clear()

                    elif action == "karaoke" or action == "viz":
                        view = "karaoke"; start_visualizer()

                    elif action == "new_playlist":
                        mode = "new_playlist"; input_txt = ""

                    elif action.startswith("pl:"):
                        # buka custom playlist
                        pl_name        = action[3:]
                        current_cpl    = pl_name
                        is_custom_view = True
                        pl_src         = get_playlist_songs(pl_name)
                        pl_title       = pl_name
                        pl_sel_v       = 0
                        view           = "playlist"
                        active_panel   = "playlist"

                    else:
                        # All Songs
                        is_custom_view = False
                        current_cpl    = ""
                        pl_src   = list(playlist)
                        pl_title = "All Songs"
                        pl_sel_v = 0
                        pl_sel   = 0
                        active_panel = "playlist"

                elif pl_src:
                    try:
                        real_idx = playlist.index(pl_src[pl_sel_v])
                        play_song(real_idx)
                    except ValueError:
                        pass

    stop_visualizer()
    os.system("cls")
    console.print(f"\n[{C_DIM}]bye ♪[/{C_DIM}]\n")

# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    os.system("cls")
    load_playlists()
    console.print(f"[{C_DIM}]scanning ./music ...[/{C_DIM}]")
    scan_music()
    lrc = sum(1 for p in playlist if p.with_suffix(".lrc").exists())
    console.print(f"[{C_GREEN}]{len(playlist)} songs[/{C_GREEN}] [{C_DIM}]({lrc} .lrc)[/{C_DIM}]")
    pln = len(custom_playlists)
    if pln:
        console.print(f"[{C_CYAN}]{pln} custom playlist(s)[/{C_CYAN}]")
    if not HAS_LYRICS:
        console.print(f"[{C_YELLOW}]tip: pip install syncedlyrics[/{C_YELLOW}]")
    if not HAS_YTDLP:
        console.print(f"[{C_YELLOW}]tip: pip install yt-dlp  (untuk download YouTube)[/{C_YELLOW}]")
    if not HAS_VIZ:
        console.print(f"[{C_YELLOW}]tip: pip install numpy pydub  (untuk visualizer)[/{C_YELLOW}]")
    time.sleep(0.5)
    main_loop()

# pip install pygame rich syncedlyrics mutagen yt-dlp numpy pydub