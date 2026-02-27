"""GTK Application shell for image-viewer.

Manages the main window, mode switching between thumbnail and slideshow views,
and coordinates scanning/database operations.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gio, Gdk

from .models import AppConfig, ImageInfo
from .database import MultiDatabase
from .scanner import scan_and_store, get_base_dirs
from .sorting import sort_images


class ImageViewerApp(Gtk.Application):
    """Main GTK Application."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__(
            application_id="io.github.image-viewer",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.config = config
        self.db: Optional[MultiDatabase] = None
        self.images: list[ImageInfo] = []
        self._main_window: Optional["MainWindow"] = None

        self.connect("activate", self._on_activate)

    def _on_activate(self, app: Gtk.Application) -> None:
        """Called when the application is activated."""
        # Open database
        base_dirs = get_base_dirs(self.config.paths)
        self.db = MultiDatabase(base_dirs)
        self.db.connect()

        # Create main window
        self._main_window = MainWindow(self, self.config)
        self._main_window.present()

        # Scan images in background thread
        self._scan_images_async()

    def _scan_images_async(self) -> None:
        """Scan images in a background thread, then update the UI."""
        def _progress(filepath: str, count: int) -> None:
            """Called from background thread for each image found during walk."""
            # Throttle UI updates to every 50 images to avoid flooding the main thread
            if count % 50 == 0 or count == 1:
                GLib.idle_add(self._on_scan_progress, count, filepath)

        def _scan():
            images = scan_and_store(
                self.config.paths,
                self.db,
                recursive=self.config.recursive,
                progress_callback=_progress,
            )
            sorted_images = sort_images(images, self.config.sort)
            # Schedule UI update on main thread
            GLib.idle_add(self._on_scan_complete, sorted_images)

        thread = threading.Thread(target=_scan, daemon=True)
        thread.start()

    def _on_scan_progress(self, count: int, filepath: str) -> bool:
        """Called on the main thread periodically during scanning."""
        if self._main_window:
            self._main_window.update_scan_progress(count, filepath)
        return False

    def _on_scan_complete(self, images: list[ImageInfo]) -> bool:
        """Called on the main thread when scanning is complete."""
        self.images = images
        if self._main_window:
            self._main_window.on_images_loaded(images)
        return False  # Remove from idle queue

    def refresh_images(self) -> None:
        """Re-sort and refresh the image list (e.g., after rating change)."""
        if self.db:
            all_images = self.db.get_all_images()
            self.images = sort_images(all_images, self.config.sort)
            if self._main_window:
                self._main_window.on_images_loaded(self.images)

    def update_rating(self, filepath: str, delta: int) -> int:
        """Change the rating of an image by delta (-1 or +1). Returns new rating."""
        if not self.db:
            return 0
        image = self.db.get_image(filepath)
        if image is None:
            return 0
        new_rating = max(0, min(5, image.rating + delta))
        self.db.update_rating(filepath, new_rating)
        # Update in-memory list
        for img in self.images:
            if img.filepath == filepath:
                img.rating = new_rating
                break
        return new_rating

    def mark_viewed(self, filepath: str) -> None:
        """Mark an image as viewed."""
        if not self.db:
            return
        self.db.mark_viewed(filepath)
        for img in self.images:
            if img.filepath == filepath:
                img.viewed = True
                img.view_count += 1
                break

    def do_shutdown(self) -> None:
        """Clean up on application shutdown."""
        # Clean up views before closing
        if self._main_window:
            if self._main_window._slide_view:
                self._main_window._slide_view.cleanup()
            if self._main_window._thumb_view:
                self._main_window._thumb_view.cleanup()
        if self.db:
            self.db.close()
        Gtk.Application.do_shutdown(self)


class MainWindow(Gtk.ApplicationWindow):
    """Main application window that hosts either the thumbnail or slideshow view."""

    def __init__(self, app: ImageViewerApp, config: AppConfig) -> None:
        super().__init__(application=app, title="image-viewer")
        self.app = app
        self.config = config

        self.set_default_size(1200, 800)

        # Stack to switch between views
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.set_child(self._stack)

        # Loading screen with spinner + progress label
        loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        loading_box.set_halign(Gtk.Align.CENTER)
        loading_box.set_valign(Gtk.Align.CENTER)

        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        loading_box.append(spinner)

        self._loading_label = Gtk.Label(label="Scanning images…")
        self._loading_label.add_css_class("loading-label")
        loading_box.append(self._loading_label)

        self._loading_sub = Gtk.Label(label="")
        self._loading_sub.add_css_class("loading-sub")
        self._loading_sub.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        self._loading_sub.set_max_width_chars(60)
        loading_box.append(self._loading_sub)

        self._stack.add_named(loading_box, "loading")
        self._stack.set_visible_child_name("loading")

        # Views (created lazily)
        self._thumb_view: Optional["ThumbnailView"] = None
        self._slide_view: Optional["SlideshowView"] = None

        # Track current slideshow window size for zoom-to-fit
        self._slideshow_size: tuple[int, int] = (1200, 800)

        # Connect resize signal for slideshow sticky size
        # In GTK4, notify::default-width/height fires when user resizes the window
        self.connect("notify::default-width", self._on_size_changed)
        self.connect("notify::default-height", self._on_size_changed)

        # Handle window close request (X button)
        self.connect("close-request", self._on_close_request)

        # Global keyboard handler for window-level keys (ESC in fullscreen, etc.)
        self._key_ctrl = Gtk.EventControllerKey()
        self._key_ctrl.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(self._key_ctrl)

    def _on_close_request(self, widget) -> bool:
        """Handle window close request - cleanup before closing."""
        if self._slide_view:
            self._slide_view.cleanup()
        if self._thumb_view:
            self._thumb_view.cleanup()
        return False  # Allow the close to proceed

    def _on_size_changed(self, widget, param) -> None:
        """Update sticky slideshow size when window is resized."""
        w = self.get_width()
        h = self.get_height()
        if w > 0 and h > 0 and (w, h) != self._slideshow_size:
            self._slideshow_size = (w, h)
            if self._slide_view:
                self._slide_view.on_window_resized(w, h)

    def _on_window_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle window-level keyboard events (e.g., ESC in fullscreen)."""
        # If slideshow is visible, delegate to slideshow keyboard handler
        if self._stack.get_visible_child_name() == "slideshow" and self._slide_view:
            # Let slideshow handle its own keys first
            handled = self._slide_view.handle_key(keyval)
            if handled:
                return True
        
        # If thumbnails is visible, delegate to thumbnail keyboard handler
        if self._stack.get_visible_child_name() == "thumbnails" and self._thumb_view:
            handled = self._thumb_view.handle_key(keyval)
            if handled:
                return True
        
        # ESC to exit fullscreen or return to thumbnails from slideshow
        if keyval == Gdk.KEY_Escape:
            if self.is_fullscreen():
                self.unfullscreen()
                return True
            elif self._stack.get_visible_child_name() == "slideshow" and self._slide_view:
                self._slide_view.stop_playing()
                # Hide slideshow immediately
                self._stack.set_visible_child_name("loading")
                # Defer thumbnail loading
                GLib.idle_add(self.show_thumbnails)
                return True
        return False

    def update_scan_progress(self, count: int, filepath: str) -> None:
        """Update the loading screen with current scan progress."""
        self._loading_label.set_label(f"Scanning… {count} images found")
        # Show just the filename, not the full path
        self._loading_sub.set_label(os.path.basename(filepath))

    def on_images_loaded(self, images: list[ImageInfo]) -> None:
        """Called when image scanning is complete."""
        if self.config.start_slideshow:
            self.show_slideshow(images, start_index=0, auto_play=True)
        else:
            self.show_thumbnails(images)

    def show_thumbnails(self, images: Optional[list[ImageInfo]] = None) -> None:
        """Switch to the thumbnail view."""
        from .thumbnail_view import ThumbnailView

        if images is None:
            images = self.app.images

        # Clean up slideshow view first
        if self._slide_view is not None:
            self._slide_view.cleanup()

        # Ensure thumbnail view exists
        if self._thumb_view is None:
            self._thumb_view = ThumbnailView(self, self.app, self.config)
            self._stack.add_named(self._thumb_view, "thumbnails")

        # Switch view
        self._stack.set_visible_child_name("thumbnails")
        self.set_title("image-viewer — Thumbnails")
        
        # Load images (will skip rebuild if images haven't changed)
        self._thumb_view.load_images(images)

    def show_slideshow(
        self,
        images: Optional[list[ImageInfo]] = None,
        start_index: int = 0,
        auto_play: bool = False,
    ) -> None:
        """Switch to the slideshow view."""
        from .slideshow_view import SlideshowView

        if images is None:
            images = self.app.images

        # Clean up thumbnail view before switching
        if self._thumb_view is not None:
            self._thumb_view.cleanup()

        if self._slide_view is None:
            self._slide_view = SlideshowView(self, self.app, self.config)
            self._stack.add_named(self._slide_view, "slideshow")

        self._slide_view.load_images(images, start_index=start_index)
        self._stack.set_visible_child_name("slideshow")

        if self.config.fullscreen:
            self.fullscreen()

        # Auto-start slideshow playback if requested
        if auto_play and self._slide_view:
            self._slide_view.start_playing()

    def get_slideshow_size(self) -> tuple[int, int]:
        """Return the current sticky slideshow size."""
        return self._slideshow_size

    def show_settings(self) -> None:
        """Show a settings dialog."""
        from .config import get_default_config
        
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="Settings",
        )
        
        # Build settings content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        
        # Base display time
        time_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        time_label = Gtk.Label(label="Base display time (seconds):")
        time_box.append(time_label)
        
        time_scale = Gtk.Scale()
        time_scale.set_digits(1)
        time_scale.set_range(0.5, 30)
        time_scale.set_value(self.config.slideshow_time)
        time_scale.set_size_request(150, 24)
        time_box.append(time_scale)
        
        time_value = Gtk.Label(label=f"{self.config.slideshow_time:.1f}s")
        time_scale.connect("value-changed", lambda s: time_value.set_label(f"{s.get_value():.1f}s"))
        time_box.append(time_value)
        content.append(time_box)
        
        # Rating multiplier
        mult_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mult_label = Gtk.Label(label="Rating time multiplier (seconds per star):")
        mult_box.append(mult_label)
        
        mult_scale = Gtk.Scale()
        mult_scale.set_digits(1)
        mult_scale.set_range(0, 5)
        mult_scale.set_value(self.config.rating_multiplier)
        mult_scale.set_size_request(150, 24)
        mult_box.append(mult_scale)
        
        mult_value = Gtk.Label(label=f"{self.config.rating_multiplier:.1f}s")
        mult_scale.connect("value-changed", lambda s: mult_value.set_label(f"{s.get_value():.1f}s"))
        mult_box.append(mult_value)
        content.append(mult_box)
        
        # Thumbnail cache size
        cache_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cache_label = Gtk.Label(label="Thumbnail cache size (pixels):")
        cache_box.append(cache_label)
        
        cache_scale = Gtk.Scale()
        cache_scale.set_digits(0)
        cache_scale.set_range(64, 512)
        cache_scale.set_value(self.config.thumbnail_cache_size)
        cache_scale.set_size_request(150, 24)
        cache_box.append(cache_scale)
        
        cache_value = Gtk.Label(label=f"{self.config.thumbnail_cache_size}px")
        cache_scale.connect("value-changed", lambda s: cache_value.set_label(f"{int(s.get_value())}px"))
        cache_box.append(cache_value)
        content.append(cache_box)
        
        # Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        
        def apply_and_close():
            # Save settings
            self.config.slideshow_time = time_scale.get_value()
            self.config.rating_multiplier = mult_scale.get_value()
            self.config.thumbnail_cache_size = int(cache_scale.get_value())
            
            # Save to config file
            self._save_config()
            
            dialog.destroy()
        
        def reset_to_defaults():
            defaults = get_default_config()
            time_scale.set_value(defaults.slideshow_time)
            mult_scale.set_value(defaults.rating_multiplier)
            cache_scale.set_value(defaults.thumbnail_cache_size)
        
        reset_btn = Gtk.Button(label="Reset to Defaults")
        reset_btn.connect("clicked", lambda _: reset_to_defaults())
        btn_box.append(reset_btn)
        
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.connect("clicked", lambda _: apply_and_close())
        btn_box.append(apply_btn)
        
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _: dialog.destroy())
        btn_box.append(close_btn)
        
        content.append(btn_box)
        
        dialog.set_child(content)
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()
    
    def _save_config(self) -> None:
        """Save current config to file."""
        from .config import CONFIG_FILE, CONFIG_DIR
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
        # Build config string - use lowercase TOML booleans
        config_str = f"""[defaults]
recursive = {str(self.config.recursive).lower()}
sort = "{self.config.sort}"
thumbnail_size = {self.config.thumbnail_size}
thumbnail_cache_size = {self.config.thumbnail_cache_size}
slideshow_time = {self.config.slideshow_time}
slideshow_order = "{self.config.slideshow_order}"
loop = {str(self.config.loop).lower()}
fullscreen = {str(self.config.fullscreen).lower()}
rating_multiplier = {self.config.rating_multiplier}

[appearance]
highlight_color = "{self.config.highlight_color}"
unviewed_indicator = "{self.config.unviewed_indicator}"
"""
        CONFIG_FILE.write_text(config_str)
    
    def _clear_cache_and_reload(self) -> None:
        """Clear thumbnail cache and reload images."""
        from .thumbnail_cache import clear_all_cache
        count = clear_all_cache(self.config.paths)
        print(f"Cleared {count} cached thumbnails")
        # Rescan images via the app
        self.app._scan_images_async()
    
    def rescan_and_reload(self) -> None:
        """Rescan directories and reload images (clears cache first)."""
        self._clear_cache_and_reload()
