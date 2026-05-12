"""
╔══════════════════════════════════════════════════════╗
║   MUSIC CLI PLAYER  –  Navigasi Tombol Panah ↑↓     ║
║   pip install pygame rich syncedlyrics mutagen       ║
║   python music_player.py                             ║
╚══════════════════════════════════════════════════════╝
Kontrol:
  ↑ ↓        = pilih menu / lagu
  Enter      = konfirmasi
  Esc / Q    = kembali / keluar
  ← →        = volume turun / naik (di layar player)
  Space      = pause/resume (di layar player)
  N / P      = next / prev  (di layar player)
  K          = mode karaoke (di layar player)
"""

import os, re, sys, time, threading, msvcrt
from pathlib import Path

import pygame
from rich.console import Console
from rich.live   import Live
from rich.text   import Text
from rich.panel  import Panel
from rich.layout import Layout
from rich.align  import Align
from rich.rule   import Rule
from rich        import box
from rich.table  import Table

try:
    import syncedlyrics; HAS_LYRICS = True
except ImportError:
    HAS_LYRICS = False

try:
    import mutagen.mp3; HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# ── palette ────────────────────────────────────────────
ACCENT    = "bright_cyan"
DIM       = "grey50"
SUCCESS   = "bright_green"
WARN      = "yellow"
ERR       = "bright_red"
HIGHLIGHT = "bright_white"
SEL_BG    = "on grey19"          # background item terpilih
LYRIC_CUR = "bold bright_white on dark_cyan"
LYRIC_ADJ = "bright_cyan"
LYRIC_DIM = "grey42"

MUSIC_FOLDER = Path("./music")
console      = Console()
pygame.mixer.init()

LOGO = r"""
 ███╗   ███╗██╗   ██╗███████╗██╗ ██████╗
 ████╗ ████║██║   ██║██╔════╝██║██╔════╝
 ██╔████╔██║██║   ██║███████╗██║██║
 ██║╚██╔╝██║██║   ██║╚════██║██║██║
 ██║ ╚═╝ ██║╚██████╔╝███████║██║╚██████╗
 ╚═╝     ╚═╝ ╚═════╝ ╚══════╝╚═╝ ╚═════╝  CLI Player ♪
"""

# ── state ──────────────────────────────────────────────
playlist      : list[Path]           = []
current_index : int                  = 0
paused        : bool                 = False
volume        : float                = 0.8
lyrics_lines  : list[tuple[float,str]] = []
lyrics_lock   = threading.Lock()

# ══════════════════════════════════════════════════════
#  INPUT – baca tombol tanpa Enter (Windows msvcrt)
# ══════════════════════════════════════════════════════
KEY_UP    = "UP"
KEY_DOWN  = "DOWN"
KEY_LEFT  = "LEFT"
KEY_RIGHT = "RIGHT"
KEY_ENTER = "ENTER"
KEY_ESC   = "ESC"
KEY_SPACE = "SPACE"

def read_key() -> str:
    """Baca satu tombol dari keyboard, return konstanta string."""
    ch = msvcrt.getch()
    if ch in (b'\r', b'\n'):  return KEY_ENTER
    if ch == b'\x1b':         return KEY_ESC
    if ch == b' ':            return KEY_SPACE
    if ch in (b'\xe0', b'\x00'):          # escape sequence panah
        ch2 = msvcrt.getch()
        if ch2 == b'H': return KEY_UP
        if ch2 == b'P': return KEY_DOWN
        if ch2 == b'K': return KEY_LEFT
        if ch2 == b'M': return KEY_RIGHT
        return ""
    return ch.decode(errors="ignore").lower()

# ══════════════════════════════════════════════════════
#  LRC PARSER
# ══════════════════════════════════════════════════════
_LRC_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d+))?\](.*)")

def parse_lrc(raw: str) -> list:
    result = []
    for line in raw.splitlines():
        m = _LRC_RE.match(line.strip())
        if m:
            ts = int(m.group(1))*60 + int(m.group(2)) + int(m.group(3) or 0)/100
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
                with lyrics_lock: lyrics_lines = parsed
                return
    except Exception: pass
    try:
        raw = syncedlyrics.search(song_name, plain_only=True)
        if raw:
            plain = [(i*3.0, ln) for i,ln in enumerate(raw.splitlines()) if ln.strip()]
            with lyrics_lock: lyrics_lines = plain
    except Exception: pass

def load_lyrics_async(name: str):
    threading.Thread(target=_load_lyrics_thread, args=(name,), daemon=True).start()

def current_lyric_idx() -> int:
    pos = pygame.mixer.music.get_pos() / 1000
    if pos < 0: return -1
    with lyrics_lock: lines = lyrics_lines
    idx = -1
    for i,(ts,_) in enumerate(lines):
        if ts <= pos: idx = i
        else: break
    return idx

def _try_local_lrc(p: Path):
    lrc = p.with_suffix(".lrc")
    if lrc.exists():
        try:
            parsed = parse_lrc(lrc.read_text(encoding="utf-8", errors="ignore"))
            if parsed: return parsed
        except Exception: pass
    return None

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
        load_lyrics_async(playlist[index].stem)

def toggle_pause():
    global paused
    if paused: pygame.mixer.music.unpause(); paused = False
    else:       pygame.mixer.music.pause();   paused = True

def stop_song():
    global lyrics_lines
    pygame.mixer.music.stop()
    with lyrics_lock: lyrics_lines = []

def next_song():
    global current_index
    if not playlist: return
    current_index = (current_index + 1) % len(playlist)
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
    return "─:──"

# ══════════════════════════════════════════════════════
#  SHARED RENDER HELPERS
# ══════════════════════════════════════════════════════
def clear(): os.system("cls")

def render_logo():
    console.print(Text(LOGO, style=f"bold {ACCENT}"))
    console.print(Rule(style=DIM))

def vol_bar() -> str:
    filled = int(volume * 10)
    return "█"*filled + "░"*(10-filled)

def now_playing_bar() -> Panel:
    if not playlist:
        body = Text("  ♪  Tidak ada lagu", style=DIM, justify="center")
    else:
        song  = playlist[current_index].stem
        state = f"[{WARN}]⏸ PAUSED[/{WARN}]" if paused else f"[{SUCCESS}]▶ NOW PLAYING[/{SUCCESS}]"
        pos_s = max(pygame.mixer.music.get_pos()/1000, 0)
        mm,ss = divmod(int(pos_s), 60)

        with lyrics_lock: has_lyr = bool(lyrics_lines)
        if has_lyr:
            idx = current_lyric_idx()
            with lyrics_lock:
                cur_line = lyrics_lines[idx][1][:60] if 0<=idx<len(lyrics_lines) else "…"
            lyric_row = f"\n  [{ACCENT}]♪  {cur_line}[/{ACCENT}]"
        else:
            lyric_row = f"\n  [{DIM}]♪  (memuat lirik...)[/{DIM}]" if HAS_LYRICS else ""

        body = (
            f"\n  {state}   [{DIM}]{current_index+1}/{len(playlist)}[/{DIM}]"
            f"   [{DIM}]{mm}:{ss:02d}[/{DIM}]\n\n"
            f"  [{HIGHLIGHT}]♫  {song}[/{HIGHLIGHT}]{lyric_row}\n\n"
            f"  🔊  [{ACCENT}]{vol_bar()}[/{ACCENT}] [{DIM}]{int(volume*100)}%[/{DIM}]\n"
        )
    return Panel(body, title=f"[bold {ACCENT}]── NOW PLAYING ──[/bold {ACCENT}]",
                 border_style=ACCENT, padding=(0,1))

def hint(text: str):
    console.print(Align.center(Text(text, style=DIM)))

# ══════════════════════════════════════════════════════
#  SCREEN 1 – MAIN MENU  (panah ↑↓ + Enter)
# ══════════════════════════════════════════════════════
MENU_ITEMS = [
    ("▶  Play / Pilih Lagu",  "playlist"),
    ("⏸  Pause / Resume",     "pause"),
    ("⏹  Stop",               "stop"),
    ("⏭  Next",               "next"),
    ("⏮  Previous",           "prev"),
    ("🔊  Volume",             "volume"),
    ("🎤  Mode Karaoke",       "karaoke"),
    ("🔍  Cari Lagu",          "search"),
    ("✕   Keluar",             "quit"),
]

def draw_main_menu(sel: int):
    clear()
    render_logo()
    console.print(now_playing_bar())
    console.print()

    # hint kontrol
    console.print(Text(
        "  ↑ ↓  navigasi    Enter  pilih    (di player: Space=pause  ←→=volume  N/P=skip  K=karaoke)",
        style=DIM))
    console.print()

    for i, (label, _) in enumerate(MENU_ITEMS):
        if i == sel:
            console.print(f"  [bold {ACCENT} {SEL_BG}]  › {label:<35}  [/bold {ACCENT} {SEL_BG}]")
        else:
            console.print(f"  [{DIM}]    {label}[/{DIM}]")
    console.print()

def main_menu():
    sel = 0
    while True:
        draw_main_menu(sel)
        k = read_key()
        if k == KEY_UP:    sel = (sel - 1) % len(MENU_ITEMS)
        elif k == KEY_DOWN: sel = (sel + 1) % len(MENU_ITEMS)
        elif k == KEY_ENTER:
            action = MENU_ITEMS[sel][1]
            if action == "playlist": playlist_screen()
            elif action == "pause":  toggle_pause()
            elif action == "stop":   stop_song()
            elif action == "next":   next_song()
            elif action == "prev":   prev_song()
            elif action == "volume": volume_screen()
            elif action == "karaoke": karaoke_screen()
            elif action == "search": search_screen()
            elif action == "quit":
                stop_song()
                clear()
                console.print(f"\n  [{ACCENT}]Sampai jumpa! ♪[/{ACCENT}]\n")
                sys.exit(0)
        elif k == KEY_ESC or k == "q":
            stop_song(); clear()
            console.print(f"\n  [{ACCENT}]Sampai jumpa! ♪[/{ACCENT}]\n")
            sys.exit(0)

# ══════════════════════════════════════════════════════
#  SCREEN 2 – PLAYLIST  (panah ↑↓ + Enter untuk play)
# ══════════════════════════════════════════════════════
def playlist_screen(songs: list = None, title: str = "Playlist"):
    src = songs if songs is not None else playlist
    if not src:
        clear(); render_logo()
        console.print(Panel(f"  [{WARN}]Tidak ada lagu di folder ./music[/{WARN}]",
                            border_style=WARN))
        console.print(Text("  Tekan tombol apapun untuk kembali", style=DIM))
        read_key(); return

    sel = current_index if (songs is None and 0 <= current_index < len(src)) else 0
    PAGE = 15   # baris per halaman

    while True:
        clear(); render_logo()
        console.print(now_playing_bar())
        console.print()
        console.print(Text(f"  {title}  [{sel+1}/{len(src)}]", style=f"bold {ACCENT}"))
        console.print(Text("  ↑↓ navigasi    Enter = play    Esc = kembali", style=DIM))
        console.print(Rule(style=DIM))

        # windowed display
        page_start = (sel // PAGE) * PAGE
        page_end   = min(page_start + PAGE, len(src))

        for i in range(page_start, page_end):
            song = src[i]
            dur  = get_duration(song)
            lrc  = f"[{SUCCESS}]♪[/{SUCCESS}]" if song.with_suffix(".lrc").exists() else " "
            playing_marker = f"[{SUCCESS}]▶[/{SUCCESS}]" if (songs is None and i == current_index) else " "

            if i == sel:
                console.print(
                    f"  [bold {ACCENT} {SEL_BG}]  › {playing_marker} {i:>3}.  {song.stem:<45}  {dur}  {lrc}  [/bold {ACCENT} {SEL_BG}]"
                )
            else:
                console.print(
                    f"  [{DIM}]     {playing_marker} {i:>3}.  {song.stem:<45}  {dur}  {lrc}[/{DIM}]"
                )

        if len(src) > PAGE:
            console.print(Text(f"  ... halaman {sel//PAGE+1}/{(len(src)-1)//PAGE+1}", style=DIM))

        k = read_key()
        if k == KEY_UP:    sel = (sel - 1) % len(src)
        elif k == KEY_DOWN: sel = (sel + 1) % len(src)
        elif k == KEY_ENTER:
            if songs is None:
                play_song(sel)
            else:
                # cari index asli di playlist
                try:
                    real_idx = playlist.index(src[sel])
                    play_song(real_idx)
                except ValueError: pass
        elif k in (KEY_ESC, "q"): break

# ══════════════════════════════════════════════════════
#  SCREEN 3 – VOLUME  (panah ←→)
# ══════════════════════════════════════════════════════
def volume_screen():
    while True:
        clear(); render_logo()
        console.print(now_playing_bar())
        console.print()
        filled = int(volume * 20)
        bar    = "█"*filled + "░"*(20-filled)
        console.print(Panel(
            f"\n  [{ACCENT}]{bar}[/{ACCENT}]   [{HIGHLIGHT}]{int(volume*100)}%[/{HIGHLIGHT}]\n",
            title=f"[bold {ACCENT}]── Volume ──[/bold {ACCENT}]", border_style=ACCENT, padding=(0,2)
        ))
        console.print(Text("  ← turun    → naik    Esc = kembali", style=DIM))

        k = read_key()
        if k == KEY_LEFT:  change_volume(-0.05)
        elif k == KEY_RIGHT: change_volume(0.05)
        elif k in (KEY_ESC, KEY_ENTER, "q"): break

# ══════════════════════════════════════════════════════
#  SCREEN 4 – SEARCH  (ketik keyword, lalu panah)
# ══════════════════════════════════════════════════════
def search_screen():
    keyword = ""
    while True:
        clear(); render_logo()
        console.print()
        console.print(Panel(
            f"\n  [{HIGHLIGHT}]{keyword}[/{HIGHLIGHT}][{ACCENT}]█[/{ACCENT}]\n",
            title=f"[bold {ACCENT}]── Cari Lagu ──[/bold {ACCENT}]",
            border_style=ACCENT, padding=(0,2)
        ))
        console.print(Text("  Ketik judul lagu  ·  Enter = cari  ·  Esc = batal", style=DIM))

        results = [s for s in playlist if keyword.lower() in s.stem.lower()] if keyword else []
        if results:
            console.print()
            console.print(Text(f"  {len(results)} lagu ditemukan:", style=SUCCESS))
            for i, s in enumerate(results[:8]):
                console.print(f"  [{DIM}]  {i+1}. {s.stem}[/{DIM}]")
            if len(results) > 8:
                console.print(Text(f"  ... dan {len(results)-8} lainnya", style=DIM))

        k = read_key()
        if k == KEY_ESC: break
        elif k == KEY_ENTER:
            if results:
                playlist_screen(results, title=f'Hasil: "{keyword}"')
            break
        elif k == "backspace" or k == "\x08":
            keyword = keyword[:-1]
        elif len(k) == 1 and k.isprintable():
            keyword += k

# ══════════════════════════════════════════════════════
#  SCREEN 5 – KARAOKE / PLAYER VIEW (full-screen live)
# ══════════════════════════════════════════════════════
CONTEXT = 4

def lyrics_render() -> Text:
    with lyrics_lock: lines = list(lyrics_lines)
    if not lines:
        t = Text(justify="center")
        t.append("\n\n  ♪  Memuat lirik...\n\n", style=DIM)
        return t
    idx = current_lyric_idx()
    if idx < 0: idx = 0
    start = max(0, idx - CONTEXT)
    end   = min(len(lines), idx + CONTEXT + 1)
    out   = Text(justify="center")
    out.append("\n")
    for i in range(start, end):
        _, text = lines[i]
        dist = abs(i - idx)
        if i == idx:          out.append(f"  ♫  {text}  ♫\n", style=LYRIC_CUR)
        elif dist == 1:       out.append(f"     {text}\n",     style=LYRIC_ADJ)
        elif dist == 2:       out.append(f"     {text}\n",     style=ACCENT)
        else:                 out.append(f"     {text}\n",     style=LYRIC_DIM)
    return out

def karaoke_screen():
    if not playlist:
        clear(); render_logo()
        console.print(Text("  Tidak ada lagu yang diputar. Pilih lagu dulu.", style=WARN))
        time.sleep(1.5); return

    stop_event = threading.Event()

    def _key_watcher():
        while not stop_event.is_set():
            if msvcrt.kbhit():
                k = read_key()
                if k in (KEY_ESC, "q"):      stop_event.set()
                elif k == KEY_SPACE:          toggle_pause()
                elif k == KEY_RIGHT:          change_volume(0.05)
                elif k == KEY_LEFT:           change_volume(-0.05)
                elif k in ("n", KEY_RIGHT):   next_song()
                elif k in ("p",):             prev_song()
                elif k == "n":                next_song()
            time.sleep(0.05)

    threading.Thread(target=_key_watcher, daemon=True).start()

    with Live(console=console, refresh_per_second=4, screen=True) as live:
        while not stop_event.is_set():
            song  = playlist[current_index].stem
            state = f"[{WARN}]⏸ PAUSED[/{WARN}]" if paused else f"[{SUCCESS}]▶ NOW PLAYING[/{SUCCESS}]"
            pos_s = max(pygame.mixer.music.get_pos()/1000, 0)
            mm,ss = divmod(int(pos_s), 60)

            layout = Layout()
            layout.split_column(
                Layout(name="hdr",  size=7),
                Layout(name="lyr"),
                Layout(name="foot", size=3),
            )

            hdr_text = Text(justify="center")
            hdr_text.append(f"\n  ♫  {song}  ♫\n", style=f"bold {HIGHLIGHT}")
            hdr_text.append(f"\n  {state}   ")
            hdr_text.append(f"[{ACCENT}]{mm}:{ss:02d}[/{ACCENT}]   ")
            hdr_text.append(f"🔊 [{ACCENT}]{vol_bar()}[/{ACCENT}] {int(volume*100)}%\n")
            layout["hdr"].update(Panel(hdr_text, border_style=ACCENT, padding=(0,2)))

            layout["lyr"].update(Panel(
                Align.center(lyrics_render(), vertical="middle"),
                border_style=DIM,
                title=f"[{DIM}]── Lyrics ──[/{DIM}]",
                padding=(0,4),
            ))

            layout["foot"].update(Align.center(
                Text("  Space=pause  ←→=volume  N=next  P=prev  Esc=kembali", style=DIM),
                vertical="middle",
            ))
            live.update(layout)
            time.sleep(0.25)

# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    clear(); render_logo()
    console.print(f"\n  [{DIM}]Memindai folder ./music ...[/{DIM}]")
    scan_music()
    lrc_count = sum(1 for p in playlist if p.with_suffix(".lrc").exists())
    console.print(f"  [{SUCCESS}]{len(playlist)} lagu ditemukan[/{SUCCESS}]  [{DIM}]({lrc_count} punya .lrc lokal)[/{DIM}]")
    if not HAS_LYRICS:
        console.print(f"  [{WARN}]⚠  pip install syncedlyrics  untuk lirik online[/{WARN}]")
    time.sleep(1.0)
    main_menu()

# ══════════════════════════════════════════════════════
#  INSTALL:  pip install pygame rich syncedlyrics mutagen
# ══════════════════════════════════════════════════════