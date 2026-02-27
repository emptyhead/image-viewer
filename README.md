# image-viewer

A lightweight image viewer inspired by feh, with thumbnail browsing, slideshow playback, image ratings, and viewed-status tracking.

## Features

- **Thumbnail view**: Scrollable, reflowing grid with visual indicators for viewed/unviewed status and ratings
- **Slideshow view**: Zoom-to-fit display with sticky window size, fullscreen support
- **Rating system**: 1–5 star ratings via `Numpad+`/`Numpad-` in both views
- **Viewed tracking**: Automatically marks images as viewed; unviewed images are visually highlighted
- **Flexible sorting**: Alphabetical, by directory, unviewed-first, viewed-first, by rating
- **Slideshow timing**: Base display time + rating multiplier (higher-rated images shown longer)
- **Persistent storage**: SQLite database stored alongside your images (`.image-viewer.db`)

## Requirements

- Python 3.10+
- GTK 4 (via PyGObject)
- Pillow

### Install system dependencies

**Arch Linux:**
```bash
sudo pacman -S python-gobject gtk4 python-pillow
```

**Ubuntu/Debian:**
```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 python3-pil
```

## Installation

```bash
pip install -e .
```

Or run directly:
```bash
python -m image_viewer [OPTIONS] [PATH...]
```

## Usage

```
image-viewer [OPTIONS] [PATH...]

Positional:
  PATH                    Directories or files to view (default: current dir)

Options:
  -r, --recursive         Recursively scan directories (default: on)
  --no-recursive          Disable recursive scanning
  -s, --slideshow         Start in slideshow mode instead of thumbnails
  --slideshow-time SECS   Base display time in seconds (default: 5.0)
  --slideshow-order ORDER Slideshow order: forward|backward|random (default: forward)
  --loop                  Loop slideshow (default: off)
  --sort SORT             Sort order: alpha|directory|unviewed|viewed|rating|rating-desc
                          (default: unviewed)
  --thumb-size SIZE       Thumbnail size in pixels (default: 200)
  --fullscreen            Start slideshow in fullscreen (default: on)
  --windowed              Start slideshow in windowed mode
  --rating-multiplier N   Display time multiplier per rating star (default: 0.5)
  -h, --help              Show help
  --version               Show version
```

## Keyboard Shortcuts

### Thumbnail View
| Key | Action |
|-----|--------|
| `Enter` / `Space` | Open selected image in slideshow |
| `S` | Start slideshow from current position |
| `Numpad 8/2/4/6` | Move selection up/down/left/right |
| `Numpad +` | Increase rating of selected image |
| `Numpad -` | Decrease rating of selected image |
| `Q` / `Escape` | Quit |

### Slideshow View
| Key | Action |
|-----|--------|
| `Right` / `Space` | Next image |
| `Left` / `Backspace` | Previous image |
| `P` | Play/pause auto-advance |
| `F` | Toggle fullscreen |
| `Numpad +` | Increase rating of current image |
| `Numpad -` | Decrease rating of current image |
| `Escape` / `Q` | Return to thumbnail view |

## Configuration

Config file at `./config.toml` in the app root directory:

```toml
[defaults]
recursive = true
sort = "unviewed"
thumbnail_size = 200
slideshow_time = 5.0
slideshow_order = "forward"
loop = false
fullscreen = true
rating_multiplier = 0.5

[appearance]
highlight_color = "#4a90d9"
unviewed_indicator = "border"  # "border", "dot", or "none"
```

## Database

Ratings and viewed status are stored in `.image-viewer.db` in the base image directory. If the directory is read-only, the database falls back to `~/.config/image-viewer/<hash>.db`.
