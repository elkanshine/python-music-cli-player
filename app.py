"""
cmus-style Music Player
pip install pygame rich syncedlyrics mutagen yt-dlp
python app.py

Kontrol:
  Tab        = pindah panel (Library <-> Playlist)
  ↑ ↓        = navigasi
  Enter      = play / pilih
  Space      = pause/resume
  n / p      = next / prev
  + / -      = volume naik / turun
  r          = toggle repeat (off -> all -> 1)
  /          = cari lagu
  d          = download lagu dari YouTube
  k          = tampilkan lirik
  1 2 3      = ganti tab
  q / Esc    = keluar
"""

import os, re, sys, time, threading, msvcrt
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

# ── palette cmus: hijau / cyan di atas hitam ───────────
C_GREEN   = "bright_green"
C_CYAN    = "cyan"
C_WHITE   = "white"
C_DIM     = "grey46"
C_YELLOW  = "yellow"
C_RED     = "bright_red"
C_SEL_BG  = "on grey15"
C_TITLE   = "bold bright_green"
C_BORDER  = "grey30"

MUSIC_FOLDER = Path("./music")
console      = Console()
pygame.mixer.init()

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

active_panel : str = "playlist"   # "library" | "playlist"
lib_sel      : int = 0
pl_sel       : int = 0

# ── download state ─────────────────────────────────────
dl_status    : str = ""          # pesan progress download
dl_active    : bool = False      # sedang download?
dl_lock      = threading.Lock()

LIBRARY_ITEMS = [
    ("All Songs",  "library"),
    ("Playlist",   "playlist_view"),
    ("──────────", "sep"),
    ("Search /",   "search"),
    ("Download d", "download"),
    ("──────────", "sep2"),
    ("Lyrics  k",  "karaoke"),
]

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
#  DOWNLOAD (yt-dlp)
# ══════════════════════════════════════════════════════
def _dl_progress_hook(d: dict):
    global dl_status
    status = d.get("status", "")
    if status == "downloading":
        pct    = d.get("_percent_str", "??%").strip()
        speed  = d.get("_speed_str",  "??").strip()
        eta    = d.get("_eta_str",    "??").strip()
        fname  = Path(d.get("filename", "")).stem[:30]
        with dl_lock:
            dl_status = f"dl  {fname}  {pct}  {speed}  eta {eta}"
    elif status == "finished":
        with dl_lock:
            dl_status = f"dl  selesai — konversi ke mp3..."
    elif status == "error":
        with dl_lock:
            dl_status = f"dl  ERROR"

def _download_thread(query: str):
    global dl_active, dl_status
    if not HAS_YTDLP:
        with dl_lock: dl_status = "ERROR: yt-dlp tidak terinstall  (pip install yt-dlp)"
        time.sleep(3)
        with dl_lock: dl_status = ""; dl_active = False
        return

    # jika bukan URL YouTube, cari dulu
    is_url = query.startswith("http://") or query.startswith("https://")
    search_q = query if is_url else f"ytsearch1:{query}"

    ydl_opts = {
        "format":           "bestaudio/best",
        "outtmpl":          str(MUSIC_FOLDER / "%(title)s.%(ext)s"),
        "postprocessors":   [{
            "key":            "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "progress_hooks":   [_dl_progress_hook],
        "quiet":            True,
        "no_warnings":      True,
        "noplaylist":       True,
    }

    try:
        with dl_lock: dl_status = f"mencari: {query[:40]}..."
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([search_q])
        with dl_lock: dl_status = "selesai! tekan r untuk refresh playlist"
        time.sleep(2.5)
    except Exception as e:
        with dl_lock: dl_status = f"ERROR: {str(e)[:60]}"
        time.sleep(3)

    with dl_lock:
        dl_status = ""
        dl_active = False
    # refresh playlist otomatis
    scan_music()

def start_download(query: str):
    global dl_active
    if dl_active: return
    dl_active = True
    threading.Thread(target=_download_thread, args=(query,), daemon=True).start()

# ══════════════════════════════════════════════════════
#  RENDER COMPONENTS
# ══════════════════════════════════════════════════════
def fmt_time(sec: float) -> str:
    m, s = divmod(int(max(sec, 0)), 60)
    return f"{m}:{s:02d}"

def progress_bar(width: int = 40) -> str:
    if not playlist: return f"[{C_DIM}]{'─'*width}[/{C_DIM}]"
    pos = max(pygame.mixer.music.get_pos()/1000, 0)
    dur = 0
    if HAS_MUTAGEN:
        try: dur = mutagen.mp3.MP3(str(playlist[current_index])).info.length
        except: pass
    frac  = (pos / dur) if dur > 0 else 0
    done  = round(frac * width)
    empty = width - done
    bar   = "━" * done + ("╸" if empty else "") + "─" * max(0, empty - 1)
    return f"[{C_GREEN}]{bar[:done+1]}[/{C_GREEN}][{C_DIM}]{bar[done+1:]}[/{C_DIM}]"

def vol_bar(width: int = 8) -> str:
    filled = round(volume * width)
    return (f"[{C_GREEN}]" + "█" * filled + f"[/{C_GREEN}]"
            + f"[{C_DIM}]" + "░" * (width - filled) + f"[/{C_DIM}]")

# ── Tab bar ────────────────────────────────────────────
def make_tab_bar(view: str) -> Text:
    tabs = [("1:library", "library"), ("2:playlist", "playlist"), ("3:lyrics", "karaoke")]
    t = Text()
    for label, key in tabs:
        if view == key:
            t.append(f" {label} ", style=f"bold {C_GREEN} on grey19")
        else:
            t.append(f" {label} ", style=C_DIM)
        t.append(" ")
    return t

# ── Status bar ─────────────────────────────────────────
def make_status_bar(search_mode: bool = False, kw: str = "",
                    dl_mode: bool = False, dl_kw: str = "") -> Text:
    # download input mode
    if dl_mode:
        t = Text()
        t.append(" d ", style=f"bold {C_GREEN}")
        t.append("YouTube: ", style=C_DIM)
        t.append(dl_kw, style=C_WHITE)
        t.append("█", style=C_GREEN)
        return t

    # search input mode
    if search_mode:
        t = Text()
        t.append(" /", style=f"bold {C_GREEN}")
        t.append(kw, style=C_WHITE)
        t.append("█", style=C_GREEN)
        return t

    # download progress
    with dl_lock:
        dls = dl_status
        dlactive = dl_active
    if dlactive or dls:
        t = Text(no_wrap=True)
        t.append(" ↓ ", style=f"bold {C_YELLOW}")
        t.append(dls, style=C_YELLOW)
        return t

    t = Text(no_wrap=True)
    if not playlist:
        t.append(" ♪  no song loaded", style=C_DIM)
        return t

    song  = playlist[current_index].stem
    pos_s = max(pygame.mixer.music.get_pos()/1000, 0)
    dur   = 0
    if HAS_MUTAGEN:
        try: dur = mutagen.mp3.MP3(str(playlist[current_index])).info.length
        except: pass

    icon  = "|| " if paused else ">> "
    icon_style = C_YELLOW if paused else C_GREEN
    t.append(f" {icon}", style=f"bold {icon_style}")
    t.append(song[:60], style=f"bold {C_WHITE}")
    t.append(f"  {fmt_time(pos_s)}", style=C_GREEN)
    if dur: t.append(f"/{fmt_time(dur)}", style=C_DIM)
    t.append(f"  vol:{int(volume*100)}%", style=C_DIM)
    t.append("  ")
    if   repeat_mode == REPEAT_ALL:   t.append("repeat:all", style=C_GREEN)
    elif repeat_mode == REPEAT_TRACK: t.append("repeat:1",   style=C_YELLOW)
    else:                             t.append("repeat:off", style=C_DIM)
    return t

# ── Footer keybinds ───────────────────────────────────
FOOTER_HINTS = (
    "Tab:panel", "↑↓:nav", "Enter:play", "spc:pause",
    "n/p:skip",  "+/-:vol", "r:repeat",  "/:search",
    "d:download", "k:lyrics", "q:quit",
)

def make_footer() -> Text:
    t = Text(no_wrap=True)
    t.append(" ")
    for i, hint in enumerate(FOOTER_HINTS):
        key, _, desc = hint.partition(":")
        t.append(key, style=f"bold {C_GREEN}")
        t.append(f":{desc}", style=C_DIM)
        if i < len(FOOTER_HINTS) - 1:
            t.append("  ")
    return t

# ── Library pane ───────────────────────────────────────
def make_library_panel(focused: bool) -> Panel:
    t = Text()
    for i, (label, key) in enumerate(LIBRARY_ITEMS):
        if key.startswith("sep"):
            t.append(f"  {'─'*14}\n", style=C_DIM)
            continue
        is_sel = (i == lib_sel)
        if is_sel and focused:
            t.append(f" ▶ {label}\n", style=f"bold {C_GREEN} {C_SEL_BG}")
        elif is_sel:
            t.append(f" ▶ {label}\n", style=f"{C_WHITE} {C_SEL_BG}")
        else:
            t.append(f"   {label}\n", style=C_DIM)

    border = C_GREEN if focused else C_BORDER
    title  = f"[{C_TITLE}]Library[/{C_TITLE}]" if focused else f"[{C_DIM}]Library[/{C_DIM}]"
    return Panel(t, title=title, border_style=border, box=box.SIMPLE_HEAVY, padding=(0,0))

# ── Playlist pane ──────────────────────────────────────
def make_playlist_panel(src: list[Path], title: str,
                         focused: bool, height: int = 20) -> Panel:
    t = Text()
    if not src:
        t.append("\n  (kosong — taruh .mp3 di folder ./music)\n", style=C_DIM)
    else:
        PAGE       = max(height - 2, 4)
        page_start = (pl_sel // PAGE) * PAGE
        page_end   = min(page_start + PAGE, len(src))

        for i in range(page_start, page_end):
            song = src[i]
            dur  = get_duration(song)
            lrc  = "♪" if song.with_suffix(".lrc").exists() else " "
            is_cur = playlist and (song == playlist[current_index])
            arrow  = "▶" if is_cur else " "

            name = song.stem
            if len(name) > 48: name = name[:46] + ".."

            line = f" {arrow} {i+1:>3}  {name:<48}  {dur:>5}  {lrc}\n"

            if i == pl_sel and focused:
                style = f"bold {C_GREEN} {C_SEL_BG}"
            elif i == pl_sel:
                style = f"{C_WHITE} {C_SEL_BG}"
            elif is_cur:
                style = f"bold {C_GREEN}"
            else:
                style = C_DIM
            t.append(line, style=style)

    border = C_GREEN if focused else C_BORDER
    cnt    = len(src) if src else 0
    title_s = (f"[{C_TITLE}]{title} ({cnt})[/{C_TITLE}]" if focused
               else f"[{C_DIM}]{title} ({cnt})[/{C_DIM}]")
    return Panel(t, title=title_s, border_style=border,
                 box=box.SIMPLE_HEAVY, padding=(0,0))

# ── Lyrics pane ────────────────────────────────────────
CONTEXT = 5

def make_lyric_panel() -> Panel:
    with lyrics_lock: lines = list(lyrics_lines)
    t = Text(justify="center")
    if not lines:
        msg = "♪  memuat lirik..." if HAS_LYRICS else "♪  (pip install syncedlyrics)"
        t.append(f"\n\n{msg}\n", style=C_DIM)
    else:
        idx   = current_lyric_idx()
        if idx < 0: idx = 0
        start = max(0, idx - CONTEXT)
        end   = min(len(lines), idx + CONTEXT + 1)
        t.append("\n")
        for i in range(start, end):
            _, text = lines[i]
            dist = abs(i - idx)
            if i == idx:    t.append(f"  ♫  {text}  ♫\n", style=f"bold {C_WHITE} on grey19")
            elif dist == 1: t.append(f"     {text}\n",     style=C_WHITE)
            elif dist == 2: t.append(f"     {text}\n",     style=C_GREEN)
            else:           t.append(f"     {text}\n",     style=C_DIM)

    return Panel(Align.center(t, vertical="middle"),
                 title=f"[{C_TITLE}]Lyrics[/{C_TITLE}]",
                 border_style=C_GREEN, box=box.SIMPLE_HEAVY)

# ── Full layout ────────────────────────────────────────
def build_layout(view: str, pl_src: list[Path], pl_title: str,
                 search_mode: bool, kw: str, height: int,
                 dl_mode: bool = False, dl_kw: str = "") -> Layout:
    root = Layout()
    root.split_column(
        Layout(name="tabs",   size=1),
        Layout(name="body"),
        Layout(name="progbar", size=1),
        Layout(name="status", size=1),
        Layout(name="footer", size=1),
    )

    root["tabs"].update(make_tab_bar(view))

    # progress bar row
    pb = Text()
    pb.append("  ")
    pb.append(progress_bar(console.width - 20 if console.width else 60))
    pb.append("  ")
    pb.append(vol_bar())
    root["progbar"].update(pb)

    body   = Layout()
    body_h = max(height - 5, 4)
    lib_w  = 20

    body.split_row(
        Layout(name="lib",  size=lib_w),
        Layout(name="main"),
    )
    body["lib"].update(make_library_panel(focused=(active_panel == "library")))

    if view == "karaoke":
        body["main"].update(make_lyric_panel())
    else:
        body["main"].update(
            make_playlist_panel(pl_src, pl_title,
                                focused=(active_panel == "playlist"),
                                height=body_h)
        )

    root["body"].update(body)
    root["status"].update(make_status_bar(search_mode, kw, dl_mode, dl_kw))
    root["footer"].update(make_footer())
    return root

# ══════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════
def main_loop():
    global active_panel, lib_sel, pl_sel

    view        = "library"
    search_mode = False
    kw          = ""
    dl_mode     = False
    dl_kw       = ""
    pl_src      : list[Path] = list(playlist)
    pl_title    = "All Songs"

    try:    rows = os.get_terminal_size().lines
    except: rows = 30

    with Live(console=console, refresh_per_second=8, screen=True) as live:
        while True:
            check_song_end()
            try:    rows = os.get_terminal_size().lines
            except: pass

            live.update(build_layout(view, pl_src, pl_title,
                                     search_mode, kw, rows, dl_mode, dl_kw))

            if not msvcrt.kbhit():
                time.sleep(0.07)
                continue

            k = read_key()

            # ── Download input mode ───────────────────
            if dl_mode:
                if k == KEY_ESC:
                    dl_mode = False; dl_kw = ""
                elif k == KEY_ENTER:
                    if dl_kw.strip():
                        start_download(dl_kw.strip())
                    dl_mode = False; dl_kw = ""
                elif k in ("\x08", "backspace"):
                    dl_kw = dl_kw[:-1]
                elif len(k) == 1 and k.isprintable():
                    dl_kw += k
                continue

            # ── Search mode ──────────────────────────
            if search_mode:
                if k == KEY_ESC:
                    search_mode = False; kw = ""
                    pl_src = list(playlist); pl_title = "All Songs"; pl_sel = 0
                elif k == KEY_ENTER:
                    search_mode = False
                    if kw:
                        pl_src  = [p for p in playlist if kw.lower() in p.stem.lower()]
                        pl_title = f"/{kw}"
                    else:
                        pl_src = list(playlist); pl_title = "All Songs"
                    pl_sel = 0; active_panel = "playlist"
                elif k in ("\x08", "backspace"):
                    kw = kw[:-1]
                elif len(k) == 1 and k.isprintable():
                    kw += k
                continue

            # ── Global hotkeys ───────────────────────
            if   k in ("q", KEY_ESC):         stop_music(); break
            elif k == KEY_SPACE:               toggle_pause()
            elif k == "n":                     next_song()
            elif k == "p":                     prev_song()
            elif k in ("+", "="):              change_volume(0.05)
            elif k == "-":                     change_volume(-0.05)
            elif k == "r":
                toggle_repeat()
                # juga refresh playlist jika baru selesai download
                pl_src = list(playlist); pl_title = "All Songs"
            elif k == "/":
                search_mode = True; kw = ""
                pl_src = list(playlist); pl_title = "All Songs"; pl_sel = 0
            elif k == "d":
                if not dl_active:
                    dl_mode = True; dl_kw = ""
            elif k == "1":                     view = "library"
            elif k == "2":                     view = "library"; active_panel = "playlist"
            elif k == "3":                     view = "karaoke"
            elif k == "k":                     view = "karaoke"

            # ── Tab: switch panel ────────────────────
            elif k == KEY_TAB:
                active_panel = "playlist" if active_panel == "library" else "library"

            # ── Navigation ───────────────────────────
            elif k == KEY_UP:
                if active_panel == "library":
                    lib_sel = (lib_sel - 1) % len(LIBRARY_ITEMS)
                    while LIBRARY_ITEMS[lib_sel][1].startswith("sep"):
                        lib_sel = (lib_sel - 1) % len(LIBRARY_ITEMS)
                elif pl_src:
                    pl_sel = max(0, pl_sel - 1)

            elif k == KEY_DOWN:
                if active_panel == "library":
                    lib_sel = (lib_sel + 1) % len(LIBRARY_ITEMS)
                    while LIBRARY_ITEMS[lib_sel][1].startswith("sep"):
                        lib_sel = (lib_sel + 1) % len(LIBRARY_ITEMS)
                elif pl_src:
                    pl_sel = min(len(pl_src) - 1, pl_sel + 1)

            # ── Enter ────────────────────────────────
            elif k == KEY_ENTER:
                if active_panel == "library":
                    action = LIBRARY_ITEMS[lib_sel][1]
                    if action == "search":
                        search_mode = True; kw = ""
                        pl_src = list(playlist); pl_title = "All Songs"; pl_sel = 0
                    elif action == "download":
                        if not dl_active:
                            dl_mode = True; dl_kw = ""
                    elif action == "karaoke":
                        view = "karaoke"
                    else:
                        pl_src = list(playlist); pl_title = "All Songs"
                        pl_sel = 0; active_panel = "playlist"
                elif pl_src:
                    try:
                        real_idx = playlist.index(pl_src[pl_sel])
                        play_song(real_idx)
                    except ValueError:
                        pass

    os.system("cls")
    console.print(f"\n[{C_DIM}]bye ♪[/{C_DIM}]\n")

# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    os.system("cls")
    console.print(f"[{C_DIM}]scanning ./music ...[/{C_DIM}]")
    scan_music()
    lrc = sum(1 for p in playlist if p.with_suffix(".lrc").exists())
    console.print(f"[{C_GREEN}]{len(playlist)} songs found[/{C_GREEN}] [{C_DIM}]({lrc} with .lrc)[/{C_DIM}]")
    if not HAS_LYRICS:
        console.print(f"[{C_YELLOW}]tip: pip install syncedlyrics  for online lyrics[/{C_YELLOW}]")
    if not HAS_YTDLP:
        console.print(f"[{C_YELLOW}]tip: pip install yt-dlp  for YouTube download[/{C_YELLOW}]")
    time.sleep(0.6)
    main_loop()

# pip install pygame rich syncedlyrics mutagen yt-dlp