"""Slideshow view for image-viewer.

Displays images one at a time with:
- Zoom-to-fit with sticky window size
- Forward/backward/random navigation
- Auto-advance with rating-based timing
- Loop support
- Rating via Numpad+/-
- Viewed tracking (marks image as viewed after 1 second)
- Fullscreen toggle
- Brief overlay notifications for rating changes
"""

from __future__ import annotations

import random
import time
from typing import Optional, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf

from .models import AppConfig, ImageInfo

if TYPE_CHECKING:
    from .app import MainWindow, ImageViewerApp


class SlideshowView(Gtk.Overlay):
    """Full-window slideshow view with overlay notifications."""

    def __init__(
        self,
        window: "MainWindow",
        app: "ImageViewerApp",
        config: AppConfig,
    ) -> None:
        super().__init__()
        self._window = window
        self._app = app
        self._config = config

        self._images: list[ImageInfo] = []
        self._current_index: int = 0
        self._playing: bool = False
        self._advance_timer_id: Optional[int] = None
        self._viewed_timer_id: Optional[int] = None
        self._display_start_time: float = 0.0

        # Sticky window size for zoom-to-fit
        self._display_width: int = 1200
        self._display_height: int = 800

        # Random order history (for random mode without immediate repeats)
        self._random_history: list[int] = []
        self._random_pos: int = -1

        # ---- Main image display ----
        self._picture = Gtk.Picture()
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._picture.set_can_shrink(True)
        self._picture.set_hexpand(True)
        self._picture.set_vexpand(True)
        # Black background
        self._picture.add_css_class("slideshow-image")

        # ---- Controls bar (bottom) ----
        self._controls_bar = self._build_controls_bar()

        # Stack for picture + controls
        self._stack = Gtk.Stack()
        self._stack.add_named(self._picture, "picture")
        self._stack.add_named(self._controls_bar, "controls")
        self._stack.set_visible_child(self._picture)
        self.set_child(self._stack)

        # ---- Info bar (bottom) ----
        self._info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._info_bar.set_valign(Gtk.Align.END)
        self._info_bar.set_halign(Gtk.Align.FILL)
        self._info_bar.add_css_class("slideshow-info-bar")
        self._info_bar.set_margin_start(12)
        self._info_bar.set_margin_end(12)
        self._info_bar.set_margin_bottom(8)

        self._info_label = Gtk.Label(label="")
        self._info_label.set_hexpand(True)
        self._info_label.set_halign(Gtk.Align.START)
        self._info_bar.append(self._info_label)

        self._rating_label = Gtk.Label(label="")
        self._rating_label.add_css_class("slideshow-rating")
        self._info_bar.append(self._rating_label)

        self._play_label = Gtk.Label(label="")
        self._info_bar.append(self._play_label)

        self.add_overlay(self._info_bar)

        # ---- Paused indicator (top-right corner) ----
        self._paused_label = Gtk.Label(label="⏸ PAUSED")
        self._paused_label.set_halign(Gtk.Align.END)
        self._paused_label.set_valign(Gtk.Align.START)
        self._paused_label.add_css_class("paused-indicator")
        self._paused_label.set_margin_start(12)
        self._paused_label.set_margin_end(12)
        self._paused_label.set_margin_top(12)
        self._paused_label.set_visible(False)
        self.add_overlay(self._paused_label)

        # ---- Rating notification overlay (center) ----
        self._notif_label = Gtk.Label(label="")
        self._notif_label.set_halign(Gtk.Align.CENTER)
        self._notif_label.set_valign(Gtk.Align.CENTER)
        self._notif_label.add_css_class("rating-notification")
        self._notif_label.set_visible(False)
        self.add_overlay(self._notif_label)
        self._notif_timer_id: Optional[int] = None

        # ---- Keyboard controller ----
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        # Apply CSS
        self._apply_css()

    def _apply_css(self) -> None:
        css = """
        .slideshow-image {
            background-color: black;
        }
        .slideshow-info-bar {
            background-color: alpha(black, 0.6);
            border-radius: 6px;
            padding: 4px 8px;
            color: white;
        }
        .slideshow-rating {
            color: #f0a500;
            font-size: 16px;
        }
        .rating-notification {
            background-color: alpha(black, 0.75);
            color: white;
            font-size: 32px;
            border-radius: 12px;
            padding: 16px 24px;
        }
        .paused-indicator {
            background-color: alpha(black, 0.5);
            color: white;
            font-size: 14px;
            border-radius: 4px;
            padding: 4px 8px;
        }
        .slideshow-controls {
            background-color: alpha(black, 0.8);
            padding: 8px 12px;
            border-radius: 8px;
        }
        .control-btn {
            min-width: 36px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_controls_bar(self) -> Gtk.Box:
        """Build the bottom controls bar for slideshow."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("slideshow-controls")
        bar.set_margin_start(12)
        bar.set_margin_end(12)
        bar.set_margin_bottom(12)

        # Play/Pause button
        self._play_pause_btn = Gtk.Button(label="▶")
        self._play_pause_btn.set_tooltip_text("Play/Pause (P)")
        self._play_pause_btn.connect("clicked", self._on_play_pause_clicked)
        bar.append(self._play_pause_btn)

        # Order dropdown
        order_label = Gtk.Label(label="Order:")
        bar.append(order_label)

        self._order_combo = Gtk.DropDown()
        order_strings = Gtk.StringList()
        for o in ["forward", "backward", "random"]:
            order_strings.append(o)
        self._order_combo.set_model(order_strings)
        # Set current
        orders = ["forward", "backward", "random"]
        try:
            idx = orders.index(self._config.slideshow_order)
            self._order_combo.set_selected(idx)
        except ValueError:
            pass
        self._order_combo.connect("notify::selected", self._on_order_changed)
        bar.append(self._order_combo)

        # Loop toggle
        self._loop_btn = Gtk.ToggleButton(label="↺")
        self._loop_btn.set_tooltip_text("Loop (L)")
        self._loop_btn.set_active(self._config.loop)
        self._loop_btn.connect("toggled", self._on_loop_toggled)
        bar.append(self._loop_btn)

        # Time adjustment
        time_label = Gtk.Label(label="Time:")
        bar.append(time_label)

        self._time_scale = Gtk.Scale()
        self._time_scale.set_digits(1)
        self._time_scale.set_range(0.5, 30.0)
        self._time_scale.set_value(self._config.slideshow_time)
        self._time_scale.set_size_request(100, 24)
        self._time_scale.connect("value-changed", self._on_time_changed)
        bar.append(self._time_scale)

        # Time display
        self._time_label = Gtk.Label(label=f"{self._config.slideshow_time:.1f}s")
        bar.append(self._time_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        # Back to thumbnails button
        back_btn = Gtk.Button(label="⬅ Thumbnails")
        back_btn.set_tooltip_text("Back to thumbnails (Esc/Q)")
        back_btn.connect("clicked", self._on_back_clicked)
        bar.append(back_btn)

        # Settings button
        settings_btn = Gtk.Button(label="⚙")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._on_settings_clicked)
        bar.append(settings_btn)

        return bar

    def _on_settings_clicked(self, btn: Gtk.Button) -> None:
        """Show settings dialog."""
        self._window.show_settings()

    def _on_play_pause_clicked(self, btn: Gtk.Button) -> None:
        """Handle play/pause button click."""
        self._toggle_play()

    def _on_order_changed(self, combo: Gtk.DropDown, _param) -> None:
        """Handle order dropdown change."""
        orders = ["forward", "backward", "random"]
        idx = combo.get_selected()
        if 0 <= idx < len(orders):
            self._config.slideshow_order = orders[idx]
            if self._config.slideshow_order == "random":
                self._random_history = []
                self._random_pos = -1
            self._update_info_bar()

    def _on_loop_toggled(self, btn: Gtk.ToggleButton) -> None:
        """Handle loop toggle."""
        self._config.loop = btn.get_active()
        self._update_info_bar()

    def _on_time_changed(self, scale: Gtk.Scale) -> None:
        """Handle time scale change."""
        from .config import save_config
        self._config.slideshow_time = scale.get_value()
        self._time_label.set_label(f"{self._config.slideshow_time:.1f}s")
        self._update_info_bar()
        save_config(self._config)

    def _on_back_clicked(self, btn: Gtk.Button) -> None:
        """Handle back to thumbnails button click."""
        self._stop_advance()
        if self._window.is_fullscreen():
            self._window.unfullscreen()
        # Hide slideshow immediately
        self._window._stack.set_visible_child_name("loading")
        # Defer thumbnail loading
        GLib.idle_add(self._window.show_thumbnails)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def load_images(self, images: list[ImageInfo], start_index: int = 0) -> None:
        """Load a new image list and display the image at start_index."""
        self._images = images
        self._current_index = max(0, min(start_index, len(images) - 1))
        self._random_history = []
        self._random_pos = -1
        self._stop_advance()
        self._show_current()

    def on_window_resized(self, width: int, height: int) -> None:
        """Called when the window is resized. Updates sticky display size."""
        self._display_width = width
        self._display_height = height
        # Re-display current image at new size
        if self._images:
            self._show_current()

    # -------------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------------

    def _show_current(self) -> None:
        """Display the current image."""
        if not self._images:
            return

        img = self._images[self._current_index]
        self._load_image(img)
        self._update_info_bar()
        self._start_viewed_timer(img)
        self._display_start_time = time.time()

        # Update window title
        self._window.set_title(
            f"image-viewer — {img.filename} "
            f"[{self._current_index + 1}/{len(self._images)}]"
        )

    def _load_image(self, img: ImageInfo) -> None:
        """Load and display an image, scaled to fit the display size."""
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                img.filepath,
                self._display_width,
                self._display_height,
                True,  # preserve aspect ratio
            )
            self._picture.set_pixbuf(pixbuf)
        except Exception as e:
            # Show error placeholder
            self._picture.set_pixbuf(None)
            print(f"Error loading image {img.filepath}: {e}")

    def _go_next(self) -> None:
        """Advance to the next image."""
        if not self._images:
            return

        if self._config.slideshow_order == "random":
            self._go_random_forward()
        elif self._config.slideshow_order == "backward":
            self._go_prev_linear()
        else:
            self._go_next_linear()

    def _go_prev(self) -> None:
        """Go to the previous image."""
        if not self._images:
            return

        if self._config.slideshow_order == "random":
            self._go_random_backward()
        elif self._config.slideshow_order == "backward":
            self._go_next_linear()
        else:
            self._go_prev_linear()

    def _go_next_linear(self) -> None:
        if self._current_index < len(self._images) - 1:
            self._current_index += 1
            self._show_current()
        elif self._config.loop:
            self._current_index = 0
            self._show_current()
        else:
            self._stop_advance()

    def _go_prev_linear(self) -> None:
        if self._current_index > 0:
            self._current_index -= 1
            self._show_current()
        elif self._config.loop:
            self._current_index = len(self._images) - 1
            self._show_current()

    def _go_random_forward(self) -> None:
        """Advance in random order, maintaining history for back navigation."""
        if len(self._images) == 1:
            return

        # If we're not at the end of history, move forward in history
        if self._random_pos < len(self._random_history) - 1:
            self._random_pos += 1
            self._current_index = self._random_history[self._random_pos]
        else:
            # Pick a new random index (avoid immediate repeat)
            candidates = [
                i for i in range(len(self._images))
                if i != self._current_index
            ]
            if not candidates:
                return
            new_index = random.choice(candidates)
            self._random_history.append(new_index)
            # Trim history to avoid unbounded growth
            if len(self._random_history) > 200:
                self._random_history = self._random_history[-100:]
                self._random_pos = len(self._random_history) - 1
            else:
                self._random_pos = len(self._random_history) - 1
            self._current_index = new_index

        self._show_current()

    def _go_random_backward(self) -> None:
        """Go back in random history."""
        if self._random_pos > 0:
            self._random_pos -= 1
            self._current_index = self._random_history[self._random_pos]
            self._show_current()

    # -------------------------------------------------------------------------
    # Auto-advance (slideshow playback)
    # -------------------------------------------------------------------------

    def _start_advance(self) -> None:
        """Start auto-advance timer."""
        self._playing = True
        self._paused_label.set_visible(False)
        self._update_info_bar()
        self._schedule_next_advance()

    def _stop_advance(self) -> None:
        """Stop auto-advance timer."""
        self._playing = False
        if self._advance_timer_id is not None:
            GLib.source_remove(self._advance_timer_id)
            self._advance_timer_id = None
        self._update_info_bar()

    def _reset_advance_timer(self) -> None:
        """Reset the auto-advance timer without stopping playback.
        
        Used when navigating manually during playback - the timer restarts
        from the new image rather than pausing.
        """
        if self._advance_timer_id is not None:
            GLib.source_remove(self._advance_timer_id)
            self._advance_timer_id = None
        if self._playing:
            self._schedule_next_advance()

    def _schedule_next_advance(self) -> None:
        """Schedule the next auto-advance based on current image's rating."""
        if not self._playing or not self._images:
            return
        img = self._images[self._current_index]
        delay_ms = int(self._config.display_time_for(img.rating) * 1000)
        self._advance_timer_id = GLib.timeout_add(delay_ms, self._on_advance_timer)

    def _on_advance_timer(self) -> bool:
        """Called when the auto-advance timer fires."""
        self._advance_timer_id = None
        if self._playing:
            # Check if we're at the end and not looping
            if (
                self._config.slideshow_order == "forward"
                and self._current_index >= len(self._images) - 1
                and not self._config.loop
            ):
                self._stop_advance()
                return False
            self._go_next()
            if self._playing:
                self._schedule_next_advance()
        return False  # Don't repeat; we reschedule manually

    def _toggle_play(self) -> None:
        """Toggle play/pause."""
        if self._playing:
            self._stop_advance()
            self._paused_label.set_visible(True)
        else:
            self._start_advance()
            self._paused_label.set_visible(False)

    def start_playing(self) -> None:
        """Start auto-advance (public method for external calls)."""
        self._start_advance()

    def stop_playing(self) -> None:
        """Stop auto-advance (public method for external calls)."""
        self._stop_advance()

    # -------------------------------------------------------------------------
    # Viewed tracking
    # -------------------------------------------------------------------------

    def _start_viewed_timer(self, img: ImageInfo) -> None:
        """Start a 1-second timer to mark the image as viewed."""
        if self._viewed_timer_id is not None:
            GLib.source_remove(self._viewed_timer_id)
            self._viewed_timer_id = None

        filepath = img.filepath
        self._viewed_timer_id = GLib.timeout_add(
            1000, self._on_viewed_timer, filepath
        )

    def _on_viewed_timer(self, filepath: str) -> bool:
        """Mark the image as viewed after 1 second of display."""
        self._viewed_timer_id = None
        self._app.mark_viewed(filepath)
        return False

    # -------------------------------------------------------------------------
    # Rating
    # -------------------------------------------------------------------------

    def _change_rating(self, delta: int) -> None:
        """Change the rating of the current image."""
        if not self._images:
            return
        img = self._images[self._current_index]
        new_rating = self._app.update_rating(img.filepath, delta)
        self._update_info_bar()
        self._show_rating_notification(new_rating)

    def _delete_current_image(self) -> None:
        """Delete the current image from disk after confirmation."""
        if not self._images:
            return
        
        img = self._images[self._current_index]
        
        # Show confirmation dialog
        dialog = Gtk.MessageDialog(
            transient_for=self._window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete image?",
        )
        dialog.set_markup(f"Delete <b>{GLib.markup_escape_text(img.display_name, -1)}</b> from disk?\nThis cannot be undone.")
        
        def on_response(dialog, response_id):
            if response_id == Gtk.ResponseType.YES:
                self._do_delete_image(img)
            dialog.destroy()
        
        dialog.connect("response", on_response)
        dialog.present()
    
    def _do_delete_image(self, img: "ImageInfo") -> None:
        """Actually delete the image from disk and update the view."""
        import os
        from pathlib import Path
        
        try:
            # Delete the file
            filepath = Path(img.filepath)
            if filepath.exists():
                filepath.unlink()
                print(f"Deleted: {img.filepath}")
            
            # Remove from database
            if self._app.db:
                self._app.db.delete_image(img.filepath)
            
            # Remove from images list
            old_index = self._current_index
            self._images.remove(img)
            self._app.images = self._images
            
            # Update view
            if not self._images:
                # No more images - return to thumbnails
                self._window.show_thumbnails()
            else:
                # Adjust index if needed
                if self._current_index >= len(self._images):
                    self._current_index = len(self._images) - 1
                self._show_current()
                self._show_notification("Image deleted")
                
        except Exception as e:
            self._show_notification(f"Error deleting: {e}")

    def _show_rating_notification(self, rating: int) -> None:
        """Show a brief overlay notification of the new rating."""
        stars = "★" * rating + "☆" * (5 - rating)
        self._notif_label.set_label(f"Rating: {stars}")
        self._notif_label.set_visible(True)

        # Cancel previous notification timer
        if self._notif_timer_id is not None:
            GLib.source_remove(self._notif_timer_id)

        self._notif_timer_id = GLib.timeout_add(
            1500, self._hide_rating_notification
        )

    def _hide_rating_notification(self) -> bool:
        self._notif_timer_id = None
        self._notif_label.set_visible(False)
        return False

    def _cycle_order(self) -> None:
        """Cycle slideshow order: forward -> backward -> random -> forward."""
        orders = ["forward", "backward", "random"]
        try:
            current_idx = orders.index(self._config.slideshow_order)
        except ValueError:
            current_idx = 0
        next_idx = (current_idx + 1) % len(orders)
        self._config.slideshow_order = orders[next_idx]
        # Reset random history when switching to random mode
        if self._config.slideshow_order == "random":
            self._random_history = []
            self._random_pos = -1
        self._update_info_bar()
        self._show_notification(f"Order: {self._config.slideshow_order}")

    def _adjust_slideshow_time(self, delta: float) -> None:
        """Adjust base slideshow time by delta seconds and save to config."""
        from .config import save_config
        self._config.slideshow_time = max(0.5, self._config.slideshow_time + delta)
        self._update_info_bar()
        self._show_notification(f"Time: {self._config.slideshow_time:.1f}s")
        # Update the time scale to reflect the change
        self._time_scale.set_value(self._config.slideshow_time)
        # Save to config file
        save_config(self._config)

    def _show_notification(self, message: str) -> None:
        """Show a brief overlay notification."""
        self._notif_label.set_label(message)
        self._notif_label.set_visible(True)

        # Cancel previous notification timer
        if self._notif_timer_id is not None:
            GLib.source_remove(self._notif_timer_id)

        self._notif_timer_id = GLib.timeout_add(
            1500, self._hide_rating_notification
        )

    # -------------------------------------------------------------------------
    # UI updates
    # -------------------------------------------------------------------------

    def _update_info_bar(self) -> None:
        """Update the info bar with current image info."""
        if not self._images:
            return
        img = self._images[self._current_index]
        total = len(self._images)
        idx = self._current_index + 1

        viewed_str = "✓" if img.viewed else "○"
        self._info_label.set_label(
            f"{idx}/{total}  {img.filename}  {viewed_str}"
        )

        stars = "★" * img.rating + "☆" * (5 - img.rating)
        self._rating_label.set_label(stars)

        play_str = "▶" if self._playing else "⏸"
        order_str = {"forward": "→", "backward": "←", "random": "⇄"}.get(
            self._config.slideshow_order, "→"
        )
        loop_str = "↺" if self._config.loop else ""
        time_str = f"{self._config.slideshow_time:.1f}s"
        self._play_label.set_label(f"{play_str} {order_str} {loop_str} {time_str}")

    # -------------------------------------------------------------------------
    # Keyboard handling
    # -------------------------------------------------------------------------

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle keyboard input in slideshow mode."""

        # Navigation - arrow keys and numpad
        if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_6):
            self._reset_advance_timer()
            self._go_next()
            return True
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_BackSpace, Gdk.KEY_KP_4):
            self._reset_advance_timer()
            self._go_prev()
            return True

        # Play/pause (spacebar)
        elif keyval == Gdk.KEY_space:
            self._toggle_play()
            return True

        # Toggle slideshow order (forward -> backward -> random -> forward)
        elif keyval in (Gdk.KEY_o, Gdk.KEY_O):
            self._cycle_order()
            return True

        # Toggle loop
        elif keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self._config.loop = not self._config.loop
            self._update_info_bar()
            self._show_notification(f"Loop: {'ON' if self._config.loop else 'OFF'}")
            return True

        # Adjust slideshow time
        elif keyval in (Gdk.KEY_equal, Gdk.KEY_KP_Equal):
            self._adjust_slideshow_time(1.0)
            return True
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Decimal):
            self._adjust_slideshow_time(-1.0)
            return True
        elif keyval in (Gdk.KEY_bracketright, Gdk.KEY_KP_9):
            self._adjust_slideshow_time(1.0)
            return True
        elif keyval in (Gdk.KEY_bracketleft, Gdk.KEY_KP_7):
            self._adjust_slideshow_time(-1.0)
            return True

        # Rating
        elif keyval in (Gdk.KEY_KP_Add, Gdk.KEY_plus):
            self._change_rating(+1)
            return True
        elif keyval in (Gdk.KEY_KP_Subtract, Gdk.KEY_minus):
            self._change_rating(-1)
            return True

        # Fullscreen toggle
        elif keyval in (Gdk.KEY_f, Gdk.KEY_F):
            if self._window.is_fullscreen():
                self._window.unfullscreen()
            else:
                self._window.fullscreen()
            return True

        # Jump to first/last image
        elif keyval == Gdk.KEY_Home:
            self._reset_advance_timer()
            self._current_index = 0
            self._show_current()
            return True
        elif keyval == Gdk.KEY_End:
            self._reset_advance_timer()
            self._current_index = len(self._images) - 1
            self._show_current()
            return True

        # Return to thumbnails
        elif keyval in (Gdk.KEY_Escape, Gdk.KEY_q, Gdk.KEY_Q):
            self._stop_advance()
            if self._window.is_fullscreen():
                self._window.unfullscreen()
            # Hide slideshow immediately
            self._window._stack.set_visible_child_name("loading")
            # Defer thumbnail loading
            GLib.idle_add(self._window.show_thumbnails)
            return True

        # Delete current image
        elif keyval == Gdk.KEY_Delete:
            self._delete_current_image()
            return True

        # Help overlay
        elif keyval in (Gdk.KEY_question, Gdk.KEY_h, Gdk.KEY_H):
            self._show_help()
            return True

        return False

    def handle_key(self, keyval: int) -> bool:
        """Public method to handle a key press from window-level controller."""
        return self._on_key_pressed(None, keyval, 0, Gdk.ModifierType(0))

    def cleanup(self) -> None:
        """Cancel all pending timers and stop playback. Call before switching views or closing."""
        # Stop auto-advance
        self._stop_advance()
        
        # Cancel viewed timer
        if self._viewed_timer_id is not None:
            GLib.source_remove(self._viewed_timer_id)
            self._viewed_timer_id = None
        
        # Cancel notification timer
        if self._notif_timer_id is not None:
            GLib.source_remove(self._notif_timer_id)
            self._notif_timer_id = None
        
        # Clear the image to free memory
        self._picture.set_pixbuf(None)

    def _show_help(self) -> None:
        """Show a brief help dialog."""
        help_text = """Navigation:
  → / KP6            Next image
  ← / Backspace / KP4  Previous image
  Home / End         First / Last image

Playback:
  Space              Play / Pause auto-advance
  O                  Cycle order (forward → backward → random)
  L                  Toggle loop

Timing:
  = / + / ] / KP9    Increase display time by 1s
  - / [ / KP7        Decrease display time by 1s

Rating:
  Numpad +           Increase rating
  Numpad -           Decrease rating

Display:
  F                  Toggle fullscreen

Other:
  Delete             Delete current image from disk
  Esc / Q            Back to thumbnails
  ? / H              This help"""
        
        # Create a simple dialog with the help text
        dialog = Gtk.Dialog()
        dialog.set_title("Slideshow Keyboard Shortcuts")
        dialog.set_transient_for(self._window)
        dialog.set_modal(True)
        dialog.set_default_size(400, 350)
        
        content = dialog.get_content_area()
        label = Gtk.Label(label=help_text)
        label.set_xalign(0)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        content.append(label)
        
        close_btn = dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        close_btn.connect("clicked", lambda _: dialog.destroy())
        
        dialog.present()
