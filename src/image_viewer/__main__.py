"""Entry point for image-viewer. Parses CLI arguments and launches the GTK application."""

from __future__ import annotations

import argparse
import os
import sys
import warnings

# Suppress harmless GTK internal warnings about slider sizes
os.environ.setdefault("G_MESSAGES_DEBUG", "")

from . import __version__
from .config import load_config
from .models import AppConfig
from .sorting import SORT_NAMES


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="image-viewer",
        description="A lightweight image viewer with thumbnails, slideshow, ratings, and viewed tracking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  image-viewer /photos                    # Open thumbnail view of /photos (recursive)
  image-viewer /photos --slideshow        # Start slideshow immediately
  image-viewer /photos --sort rating-desc # Show highest-rated images first
  image-viewer /photos --slideshow-time 3 --loop  # 3s per image, looping
        """,
    )

    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Directories or image files to view (default: current directory)",
    )

    # Scanning
    recursive_group = parser.add_mutually_exclusive_group()
    recursive_group.add_argument(
        "-r", "--recursive",
        action="store_true",
        default=None,
        help="Recursively scan directories (default: on)",
    )
    recursive_group.add_argument(
        "--no-recursive",
        action="store_true",
        default=False,
        help="Disable recursive scanning",
    )

    # Mode
    parser.add_argument(
        "-s", "--slideshow",
        action="store_true",
        default=False,
        help="Start in slideshow mode instead of thumbnail view",
    )

    # Slideshow options
    parser.add_argument(
        "--slideshow-time",
        type=float,
        metavar="SECS",
        default=None,
        help="Base display time per image in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--slideshow-order",
        choices=["forward", "backward", "random"],
        default=None,
        metavar="ORDER",
        help="Slideshow order: forward|backward|random (default: forward)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        default=None,
        help="Loop slideshow when reaching the end",
    )

    # Sorting
    parser.add_argument(
        "--sort",
        choices=SORT_NAMES,
        default=None,
        metavar="SORT",
        help=(
            "Sort order for thumbnails and slideshow: "
            + "|".join(SORT_NAMES)
            + " (default: unviewed)"
        ),
    )

    # Thumbnail options
    parser.add_argument(
        "--thumb-size",
        type=int,
        metavar="SIZE",
        default=None,
        help="Thumbnail size in pixels (default: 200)",
    )

    # Window options
    fullscreen_group = parser.add_mutually_exclusive_group()
    fullscreen_group.add_argument(
        "--fullscreen",
        action="store_true",
        default=None,
        help="Start slideshow in fullscreen (default: on)",
    )
    fullscreen_group.add_argument(
        "--windowed",
        action="store_true",
        default=False,
        help="Start slideshow in windowed mode",
    )

    # Rating multiplier
    parser.add_argument(
        "--rating-multiplier",
        type=float,
        metavar="N",
        default=None,
        help=(
            "Extra display time in seconds per rating star (default: 0.5). "
            "e.g., 5-star image with base=5s: 5 + 5*0.5 = 7.5s"
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


def apply_cli_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    """Apply CLI argument overrides to the config object."""
    # Paths
    if args.paths:
        config.paths = [os.path.abspath(p) for p in args.paths]
    else:
        config.paths = [os.getcwd()]

    # Recursive
    if args.no_recursive:
        config.recursive = False
    elif args.recursive:
        config.recursive = True
    # else: keep config file value

    # Mode
    if args.slideshow:
        config.start_slideshow = True

    # Slideshow options
    if args.slideshow_time is not None:
        config.slideshow_time = args.slideshow_time
    if args.slideshow_order is not None:
        config.slideshow_order = args.slideshow_order
    if args.loop:
        config.loop = True

    # Sort
    if args.sort is not None:
        config.sort = args.sort

    # Thumbnail size
    if args.thumb_size is not None:
        config.thumbnail_size = args.thumb_size

    # Fullscreen
    if args.windowed:
        config.fullscreen = False
    elif args.fullscreen:
        config.fullscreen = True

    # Rating multiplier
    if args.rating_multiplier is not None:
        config.rating_multiplier = args.rating_multiplier

    return config


def main() -> None:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # Load config file, then apply CLI overrides
    config = load_config()
    config = apply_cli_overrides(config, args)

    # Validate paths
    for path in config.paths:
        if not os.path.exists(path):
            print(f"Error: Path does not exist: {path}", file=sys.stderr)
            sys.exit(1)

    # Launch GTK application
    try:
        from .app import ImageViewerApp
    except ImportError as e:
        print(
            f"Error: Could not import GTK application: {e}\n"
            "Make sure PyGObject and GTK 4 are installed.\n"
            "  Arch: sudo pacman -S python-gobject gtk4\n"
            "  Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0",
            file=sys.stderr,
        )
        sys.exit(1)

    app = ImageViewerApp(config)
    exit_code = app.run(sys.argv[:1])  # Pass only program name, not our args
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
