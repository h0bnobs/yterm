# yterm

Browse and stream YouTube videos in the terminal.

## Run

```
yterm
```

(symlinked into `~/.local/bin`, or run from the project directory directly)

## Keys

| Key     | Action                                                  |
|---------|---------------------------------------------------------|
| `/`     | New search — or paste a video URL to play it directly   |
| `↑`/`↓` | Move through results                                    |
| `Enter` | Stream selected video in the terminal                   |
| `a`     | Play audio only                                         |
| `o`     | Play in the reusable mpv window (replaces it) — search stays live |
| `e`     | Enqueue the selected video after the one in the window  |
| `x`     | Stop the window player                                  |
| `c`     | Browse the selected video's channel uploads             |
| `n`     | Up next — related suggestions for the selected video    |
| `g`     | Toggle GPU / hardware decoding (off by default)         |
| `s`     | Sign in / out (browser cookies or cookies.txt)          |
| `u`     | Subscriptions feed (signed in)                          |
| `r`     | Recommended feed (signed in)                            |
| `w`     | Watch later (signed in)                                 |
| `h`     | History (signed in)                                     |
| `Esc`   | Jump from search box back to results                    |
| `?`     | Help screen with everything above                       |
| `q`     | Quit                                                    |

## Paste a URL

Type or paste a video URL into the search box and press Enter to play it
straight away instead of searching. A timestamp in the URL starts playback
at that point — all of these work:

```
https://www.youtube.com/watch?v=8XR174I_YTc&t=1322s
https://youtu.be/8XR174I_YTc?t=1h2m3s
https://www.youtube.com/watch?v=8XR174I_YTc#t=1:02:03
```

Any `http(s)` URL yt-dlp supports will play, not just YouTube.

## Playback control centre

During playback the video fills the pane from the top and a constantly
redrawn control bar sits directly under it: position / duration / volume
plus the key hints (`q` quit, `space` pause, `←/→` seek 5 s, `↑/↓` seek
1 min, `9/0` volume, `m` mute, `[ ]` speed).

## Sign in

Press `s`. yterm reuses session cookies from an installed browser (Chrome,
Chromium, Edge, Firefox, Brave, Vivaldi detected automatically) — no password
is ever entered or stored; the config file records only the browser name.

If browser cookies fail to authenticate (e.g. cookies encrypted under an old
keyring key), either log out and back in to YouTube in that browser, or export
youtube.com cookies with a "Get cookies.txt LOCALLY" extension to
`~/.config/yterm/cookies.txt` and pick "cookies file" in the sign-in menu.

## Video output and quality

yterm picks the best mpv video output for your terminal: `kitty` (kitty
graphics protocol, full pixel resolution) when running in kitty, otherwise
`tct` true-colour half-blocks which work in any terminal. Override with
`YTERM_VO=kitty|tct yterm`.

For sharp video, run yterm inside kitty, or press `o` on any video for a real
mpv window at up to 1080p. In-terminal streams fetch up to 720p
(`YTERM_MAXHEIGHT` to change).

## Search while playing

Pressing `Enter` streams a video in the terminal, which takes over the screen
until it ends. To keep browsing while you watch, press `o` instead: it plays in
a single reusable mpv window and leaves the TUI fully interactive. Search for
something else, press `n` for suggestions, then press `o` on another result to
swap it into the same window, or `e` to queue it up next. `x` stops the window
player, and the status bar shows what the window is playing while it is open.

This windowed player also runs at up to 1080p and obeys the GPU decoding
toggle, so it doubles as the high-quality way to watch.

## Up next / suggestions

Press `n` on any result to replace the list with related videos for it, like
YouTube's up-next column. When an in-terminal or audio video finishes, its
suggestions load automatically so you land on an up-next list when playback
returns to the browser. Press Enter on a suggestion to play it, or `/` to
start a new search.

Suggestions come from the video's YouTube mix and respect your sign-in when
cookies are present. They are cached per video for the session, so revisiting
the same video's suggestions is instant.

## GPU / hardware decoding

Press `g` to toggle hardware decoding (`--hwdec=auto-safe`). It is off by
default and the choice is remembered in the config file. Turning it on lowers
CPU during the decode stage and helps the windowed (`o`) path most. For
in-terminal video the decoded frames still have to be copied back to the CPU
to be drawn through the terminal graphics protocol, so the saving there is
smaller. Try it if in-terminal playback is choppy or your CPU runs hot.

## Internals

- Python + Textual TUI: `yterm.py`
- Own venv in `.venv/` with a current pip-installed yt-dlp (the system one is
  often too old for YouTube) plus `secretstorage` for Chromium cookie
  decryption — mpv is pointed at the venv yt-dlp via `ytdl_hook-ytdl_path`
- Playback: system mpv, suspending the TUI while playing; windowed playback is
  a detached process
- Config: `~/.config/yterm/config.json` (cookie source name only)
