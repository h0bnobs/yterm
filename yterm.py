#!/usr/bin/env python3
"""yterm - browse and stream YouTube videos in the terminal.

Search YouTube, arrow through results, press Enter to stream the video
inside the terminal via mpv (kitty graphics protocol where available,
ANSI half-blocks otherwise), 'a' for audio only, 'o' for a real mpv
window. Sign in with 's' (cookies from an installed browser) to unlock
the subscriptions / recommended / watch-later / history feeds.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

import yt_dlp
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_YTDLP = os.path.join(HERE, ".venv", "bin", "yt-dlp")
CONFIG_PATH = os.path.expanduser("~/.config/yterm/config.json")
COOKIES_FILE = os.path.expanduser("~/.config/yterm/cookies.txt")
SEARCH_LIMIT = 25

# Max source height for in-terminal playback. Terminal pixel area rarely
# exceeds ~720p worth of detail; raise via YTERM_MAXHEIGHT if wanted.
TERM_MAXH = int(os.environ.get("YTERM_MAXHEIGHT", "720"))
WINDOW_MAXH = 1080

BROWSER_PATHS = {
    "firefox": "~/.mozilla/firefox",
    "chrome": "~/.config/google-chrome",
    "chromium": "~/.config/chromium",
    "brave": "~/.config/BraveSoftware/Brave-Browser",
    "edge": "~/.config/microsoft-edge",
    "vivaldi": "~/.config/vivaldi",
}

FEEDS = {
    "subscriptions": ":ytsubs",
    "recommended": ":ytrec",
    "watch later": ":ytwatchlater",
    "history": ":ythistory",
}

MPV_STATUS = (
    "${?pause==yes:⏸ }${!pause==yes:▶ }"
    "${time-pos} / ${duration} (${percent-pos}%)  vol ${volume}"
    " │ q quit · spc pause · ←/→ 5s · ↑/↓ 1m · 9/0 vol · m mute · [ ] speed"
)

HELP_TEXT = """\
[b]Browser[/b]
  /        new search, or paste a video URL to play it
           (a &t=90s / &t=1h2m3s timestamp starts playback there)
  Enter    stream selected video in the terminal
  a        play audio only
  o        open in an mpv window (full quality, browse continues)
  c        list the selected video's channel uploads
  s        sign in / out (browser cookies)
  u        subscriptions feed        (signed in)
  r        recommended feed          (signed in)
  w        watch later               (signed in)
  h        history                   (signed in)
  Esc      back to results
  ?        this help
  q        quit

[b]During playback (mpv owns the terminal)[/b]
  q        stop, return to browser
  space    pause / resume
  ←/→      seek 5 s        ↑/↓   seek 1 min
  9/0      volume          m     mute
  [ / ]    playback speed  ,/.   frame step (paused)

[b]Quality[/b]
  In-terminal streams are capped at {maxh}p (YTERM_MAXHEIGHT to change).
  Terminal graphics are the limit — press o for true full quality.
""".format(maxh=TERM_MAXH)


# --------------------------------------------------------------------------
# Terminal video-output detection
# --------------------------------------------------------------------------

def detect_video_output() -> str:
    """Pick the best mpv --vo for this terminal. Override with YTERM_VO."""
    override = os.environ.get("YTERM_VO")
    if override:
        return override
    if os.environ.get("KITTY_WINDOW_ID") or "kitty" in os.environ.get("TERM", ""):
        return "kitty"
    return "tct"  # true-colour half-blocks, works everywhere


# --------------------------------------------------------------------------
# Config (stores the chosen cookie browser only — never credentials)
# --------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def detect_browsers() -> list[str]:
    return [b for b, p in BROWSER_PATHS.items() if os.path.isdir(os.path.expanduser(p))]


# --------------------------------------------------------------------------
# YouTube extraction (flat, fast)
# --------------------------------------------------------------------------

def _flat_extract(url_or_query: str, auth: str | None) -> list[dict]:
    """auth is a browser name, the literal 'file', or None."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlist_items": f"1:{SEARCH_LIMIT}",
    }
    if auth == "file":
        opts["cookiefile"] = COOKIES_FILE
    elif auth:
        opts["cookiesfrombrowser"] = (auth, None, None, None)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url_or_query, download=False)
    entries = info.get("entries") or []
    return [e for e in entries if e and e.get("url")]


def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def parse_time_token(v: str) -> int:
    """'1322', '1322s', '22m2s', '1h2m3s', '01:23', '1:02:03' -> seconds."""
    v = v.strip()
    if not v:
        return 0
    if v.isdigit():
        return int(v)
    if ":" in v:
        parts = v.split(":")
        if all(p.isdigit() for p in parts):
            secs = 0
            for p in parts:
                secs = secs * 60 + int(p)
            return secs
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", v)
    if m and any(m.groups()):
        h, mn, s = (int(x) if x else 0 for x in m.groups())
        return h * 3600 + mn * 60 + s
    return 0


def parse_start_seconds(url: str) -> int:
    """Extract a start offset from t= / start= query params or a #t= fragment."""
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    for key in ("t", "start"):
        if key in qs and qs[key]:
            return parse_time_token(qs[key][0])
    frag = parsed.fragment
    if frag.startswith("t="):
        return parse_time_token(frag[2:])
    return 0


def fmt_duration(seconds) -> str:
    if not seconds:
        return "live/?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def fmt_views(n) -> str:
    if n is None:
        return ""
    n = int(n)
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            return f"{n / div:.1f}{suffix}".replace(".0", "")
    return str(n)


# --------------------------------------------------------------------------
# Modal screens
# --------------------------------------------------------------------------

class BrowserPick(ModalScreen):
    """Choose which browser's cookies to sign in with."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, browsers: list[str], signed_in: str | None):
        super().__init__()
        self.browsers = browsers
        self.signed_in = signed_in

    def compose(self) -> ComposeResult:
        opts = [
            Option(f"Sign in with {b} cookies", id=b)
            for b in self.browsers
        ]
        if os.path.exists(COOKIES_FILE):
            opts.append(Option("Sign in with cookies file (~/.config/yterm/cookies.txt)", id="file"))
        if self.signed_in:
            opts.append(Option(f"Sign out (currently: {self.signed_in})", id="__signout__"))
        opts.append(Option("Cancel", id="__cancel__"))
        with Vertical(id="pick-box"):
            yield Static(
                "Sign in to YouTube\n\n"
                "yterm reuses the session cookies of a browser where you are\n"
                "already logged in to YouTube. No password is stored or seen.\n\n"
                "If browser cookies fail, log out and back in to YouTube in\n"
                "that browser, or export youtube.com cookies with a\n"
                "'Get cookies.txt' extension to ~/.config/yterm/cookies.txt",
                id="pick-blurb",
            )
            yield OptionList(*opts)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        if oid == "__cancel__":
            self.dismiss(None)
        elif oid == "__signout__":
            self.dismiss("__signout__")
        else:
            self.dismiss(oid)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(HELP_TEXT)

    def action_close(self) -> None:
        self.dismiss()


# --------------------------------------------------------------------------
# The TUI
# --------------------------------------------------------------------------

class YTerm(App):
    TITLE = "yterm"

    CSS = """
    #search { dock: top; margin: 0 1; }
    #status { dock: top; height: 1; padding: 0 2; color: $text-muted; }
    #results { height: 1fr; }
    BrowserPick, HelpScreen { align: center middle; }
    #pick-box, #help-box {
        width: 70; height: auto; max-height: 90%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    #pick-blurb { margin-bottom: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "Search", key_display="/"),
        Binding("enter", "play_terminal", "Play", priority=False),
        Binding("a", "play_audio", "Audio"),
        Binding("o", "play_window", "Window"),
        Binding("c", "browse_channel", "Channel"),
        Binding("s", "sign_in", "Sign in"),
        Binding("u", "feed('subscriptions')", "Subs", show=False),
        Binding("r", "feed('recommended')", "Recs", show=False),
        Binding("w", "feed('watch later')", "Later", show=False),
        Binding("h", "feed('history')", "History", show=False),
        Binding("escape", "back_to_results", "Results", show=False),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, vo: str):
        super().__init__()
        self.vo = vo
        self.entries: list[dict] = []
        self.cfg = load_config()
        self.cookies_browser: str | None = self.cfg.get("cookies_browser")

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="Search YouTube, or paste a video URL (with &t=… to seek)…",
            id="search",
        )
        yield Static("", id="status")
        yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Title", width=70, key="title")
        table.add_column("Channel", width=24, key="channel")
        table.add_column("Length", width=9, key="length")
        table.add_column("Views", width=7, key="views")
        self.refresh_idle_status()
        self.query_one("#search", Input).focus()

    # -- helpers -----------------------------------------------------------

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def auth_label(self) -> str:
        if self.cookies_browser == "file":
            return "cookies.txt"
        return self.cookies_browser or ""

    def refresh_idle_status(self) -> None:
        auth = f"signed in: {self.auth_label()}" if self.cookies_browser else "signed out"
        vo = self.vo
        if vo == "tct":
            vo += " (block art — run yterm inside kitty for sharp video, or o for a window)"
        self.set_status(f"video: {vo} ≤{TERM_MAXH}p │ {auth} │ ? for all keys")

    def in_input(self) -> bool:
        return isinstance(self.focused, Input)

    def selected_entry(self) -> dict | None:
        table = self.query_one(DataTable)
        if not self.entries or table.cursor_row is None:
            return None
        if 0 <= table.cursor_row < len(self.entries):
            return self.entries[table.cursor_row]
        return None

    def populate(self, entries: list[dict], context: str) -> None:
        self.entries = entries
        table = self.query_one(DataTable)
        table.clear()
        for e in entries:
            table.add_row(
                (e.get("title") or "?")[:68],
                (e.get("channel") or e.get("uploader") or "")[:22],
                fmt_duration(e.get("duration")),
                fmt_views(e.get("view_count")),
            )
        table.loading = False
        if entries:
            self.set_status(f"{len(entries)} results · {context} │ Enter play · a audio · o window · ? keys")
            table.focus()
        else:
            self.set_status(f"no results for {context}")

    # -- loading content ----------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if is_url(text):
            start = parse_start_seconds(text)
            entry = {"url": text, "title": text, "start": start}
            self.query_one("#search", Input).value = ""
            where = f" from {fmt_duration(start)}" if start else ""
            self.set_status(f"playing pasted URL{where}…")
            self._play("terminal", entry)
        else:
            self.start_load(f"ytsearch{SEARCH_LIMIT}:{text}", f"search {text!r}")

    def start_load(self, target: str, context: str) -> None:
        self.query_one(DataTable).loading = True
        self.set_status(f"loading {context}…")
        self.run_load(target, context)

    @work(thread=True, exclusive=True)
    def run_load(self, target: str, context: str) -> None:
        try:
            entries = _flat_extract(target, self.cookies_browser)
        except Exception as exc:
            msg = str(exc).split("\n")[0][:120]
            self.call_from_thread(self.set_status, f"{context} failed: {msg}")
            self.call_from_thread(setattr, self.query_one(DataTable), "loading", False)
            return
        self.call_from_thread(self.populate, entries, context)

    # -- actions -----------------------------------------------------------

    def action_focus_search(self) -> None:
        inp = self.query_one("#search", Input)
        inp.value = ""
        inp.focus()

    def action_back_to_results(self) -> None:
        if self.entries:
            self.query_one(DataTable).focus()

    def action_help(self) -> None:
        if not self.in_input():
            self.push_screen(HelpScreen())

    def action_feed(self, name: str) -> None:
        if self.in_input():
            return
        if not self.cookies_browser:
            self.set_status(f"{name} needs sign-in — press s")
            return
        self.start_load(FEEDS[name], name)

    def action_browse_channel(self) -> None:
        if self.in_input():
            return
        entry = self.selected_entry()
        if not entry:
            return
        url = entry.get("channel_url") or entry.get("uploader_url")
        name = entry.get("channel") or entry.get("uploader") or "channel"
        if not url:
            self.set_status("no channel link on this result")
            return
        self.start_load(url.rstrip("/") + "/videos", f"channel {name}")

    # -- sign in -----------------------------------------------------------

    def action_sign_in(self) -> None:
        if self.in_input():
            return
        browsers = detect_browsers()
        if not browsers:
            self.set_status("no supported browser profiles found")
            return
        self.push_screen(BrowserPick(browsers, self.cookies_browser), self.finish_sign_in)

    def finish_sign_in(self, choice: str | None) -> None:
        if choice is None:
            return
        if choice == "__signout__":
            self.cookies_browser = None
        else:
            self.cookies_browser = choice
        self.cfg["cookies_browser"] = self.cookies_browser
        save_config(self.cfg)
        self.refresh_idle_status()
        if self.cookies_browser:
            self.set_status(
                f"signed in via {self.auth_label()} │ u subs · r recs · w later · h history"
            )

    # -- playback ----------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_play_terminal()

    def action_play_terminal(self) -> None:
        if not self.in_input():
            self._play("terminal")

    def action_play_audio(self) -> None:
        if not self.in_input():
            self._play("audio")

    def action_play_window(self) -> None:
        if not self.in_input():
            self._play("window")

    def _mpv_base(self) -> list[str] | None:
        mpv = shutil.which("mpv")
        if not mpv:
            self.set_status("mpv is not installed — install it and restart yterm")
            return None
        # statusline=status keeps the control bar visible while muting other noise
        cmd = [mpv, "--osc=no", "--msg-level=all=error,statusline=status", "--term-osd-bar=no"]
        if os.path.exists(VENV_YTDLP):
            cmd.append(f"--script-opts=ytdl_hook-ytdl_path={VENV_YTDLP}")
        if self.cookies_browser == "file":
            cmd.append(f"--ytdl-raw-options-append=cookies={COOKIES_FILE}")
        elif self.cookies_browser:
            cmd.append(f"--ytdl-raw-options-append=cookies-from-browser={self.cookies_browser}")
        return cmd

    def _print_control_centre(self, title: str, mode_desc: str) -> None:
        cols = shutil.get_terminal_size().columns
        bar = "─" * min(cols - 1, 110)
        print(f"▶ {title}"[: cols - 1])
        print(bar)
        print(" q quit to browser │ space pause │ ←/→ seek 5s │ ↑/↓ seek 1m "
              "│ 9/0 volume │ m mute │ [ ] speed"[: cols - 1])
        print(f" {mode_desc}"[: cols - 1])
        print(bar)

    def _print_loading_line(self, title: str, mode_desc: str) -> None:
        cols = shutil.get_terminal_size().columns
        print(f"▶ {title} │ {mode_desc} │ loading…"[: cols - 1])

    def _play(self, mode: str, entry: dict | None = None) -> None:
        if entry is None:
            entry = self.selected_entry()
        if not entry:
            return
        url = entry["url"]
        title = entry.get("title") or url
        start = int(entry.get("start") or 0)

        cmd = self._mpv_base()
        if cmd is None:
            return
        if start:
            cmd.append(f"--start={start}")

        if mode == "window":
            cmd += [
                f"--ytdl-format=bestvideo[height<={WINDOW_MAXH}]+bestaudio"
                f"/best[height<={WINDOW_MAXH}]/best",
                f"--title=yterm: {title}",
                url,
            ]
            subprocess.Popen(
                cmd, start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.set_status(f"playing in window: {title[:60]} (browse continues)")
            return

        cmd.append(f"--term-status-msg={MPV_STATUS}")
        if mode == "audio":
            cmd += ["--no-video", "--ytdl-format=bestaudio/best"]
            mode_desc = "audio only"
        else:
            # Video fills the pane from the top; mpv's status line — our
            # control bar — redraws constantly on the row directly below it.
            size = shutil.get_terminal_size()
            video_rows = max(size.lines - 2, 4)
            cmd += [
                f"--vo={self.vo}",
                "--profile=sw-fast",
                f"--ytdl-format=bestvideo[height<={TERM_MAXH}]+bestaudio"
                f"/best[height<={TERM_MAXH}]/best",
            ]
            if self.vo == "tct":
                cmd.append(f"--vo-tct-height={video_rows}")
            elif self.vo == "kitty":
                cmd += ["--vo-kitty-use-shm=yes", f"--vo-kitty-rows={video_rows}", "--vo-kitty-top=1"]
            mode_desc = f"video {self.vo} ≤{TERM_MAXH}p"
        if start:
            mode_desc += f" │ from {fmt_duration(start)}"
        if self.cookies_browser:
            mode_desc += f" │ signed in: {self.auth_label()}"
        cmd.append(url)

        with self.suspend():
            os.system("clear")
            if mode == "audio":
                self._print_control_centre(title, mode_desc)
            else:
                self._print_loading_line(title, mode_desc)
            try:
                subprocess.call(cmd)
            except KeyboardInterrupt:
                pass
        self.set_status(f"finished: {title[:60]}")


def main() -> None:
    vo = detect_video_output()
    YTerm(vo).run()


if __name__ == "__main__":
    main()
