"""Thumbnail grid view for image-viewer.

Displays a scrollable, reflowing grid of image thumbnails with:
- Visual indicators for viewed/unviewed status and ratings
- Mouse and keyboard (numpad) navigation
- Rating via Numpad+/-
- Sort order selection
- Double-click or Enter to open in slideshow
"""

from __future__ import annotations

import os
import threading
from typing import Optional, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf, Pango

from .models import AppConfig, ImageInfo
from .sorting import SORT_OPTIONS, sort_images
from .thumbnail_cache import get_or_create_thumbnail

if TYPE_CHECKING:
    from .app import MainWindow, ImageViewerApp


# Stars display
STAR_FILLED = "★"
STAR_EMPTY = "☆"


def _rating_stars(rating: int) -> str:
    return STAR_FILLED * rating + STAR_EMPTY * (5 - rating)


class ThumbnailTile(Gtk.Box):
    """A single thumbnail tile: image + filename + rating + viewed indicator."""

    def __init__(
        self,
        image_info: ImageInfo,
        thumb_size: int,
        highlight_color: str,
        unviewed_indicator: str,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.image_info = image_info
        self.thumb_size = thumb_size
        self._highlight_color = highlight_color
        self._unviewed_indicator = unviewed_indicator
        self._selected = False

        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(4)
        self.set_margin_end(4)

        # Overlay for the image + unviewed indicator
        self._overlay = Gtk.Overlay()
        self.append(self._overlay)

        # Image widget
        self._picture = Gtk.Picture()
        self._picture.set_size_request(thumb_size, thumb_size)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._overlay.set_child(self._picture)

        # Unviewed dot indicator (top-right corner)
        if unviewed_indicator == "dot" and not image_info.viewed:
            dot = Gtk.Label(label="●")
            dot.add_css_class("unviewed-dot")
            dot.set_halign(Gtk.Align.END)
            dot.set_valign(Gtk.Align.START)
            dot.set_margin_end(4)
            dot.set_margin_top(4)
            self._overlay.add_overlay(dot)

        # Filename label
        name_label = Gtk.Label(label=image_info.display_name)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(20)
        name_label.set_tooltip_text(image_info.filename)
        self.append(name_label)

        # Rating label
        self._rating_label = Gtk.Label(label=_rating_stars(image_info.rating))
        self._rating_label.add_css_class("rating-stars")
        self.append(self._rating_label)

        # Apply CSS classes
        self._update_css()

    def _update_css(self) -> None:
        """Apply CSS classes based on state."""
        # Remove existing state classes
        for cls in ("selected", "unviewed-border", "viewed"):
            self.remove_css_class(cls)

        if self._selected:
            self.add_css_class("selected")
        elif self._unviewed_indicator == "border" and not self.image_info.viewed:
            self.add_css_class("unviewed-border")
        elif self.image_info.viewed:
            self.add_css_class("viewed")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_css()

    def set_size_request(self, size: int) -> None:
        """Update the picture size request for real-time resizing."""
        self.thumb_size = size
        self._picture.set_size_request(size, size)

    def set_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf) -> None:
        self._picture.set_pixbuf(pixbuf)

    def update_rating(self, rating: int) -> None:
        self.image_info.rating = rating
        self._rating_label.set_label(_rating_stars(rating))

    def update_viewed(self, viewed: bool) -> None:
        self.image_info.viewed = viewed
        self._update_css()


class ThumbnailView(Gtk.Box):
    """Scrollable thumbnail grid view."""

    def __init__(
        self,
        window: "MainWindow",
        app: "ImageViewerApp",
        config: AppConfig,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._window = window
        self._app = app
        self._config = config

        self._images: list[ImageInfo] = []
        self._tiles: list[ThumbnailTile] = []
        self._selected_index: int = 0
        self._columns: int = 1
        self._loading_cancelled: bool = False  # Flag to stop background thumbnail loading

        # Toolbar
        toolbar = self._build_toolbar()
        self.append(toolbar)

        # Scrolled window containing the flow box
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(self._scrolled)

        # FlowBox for reflowing grid
        # Use NONE selection mode to prevent FlowBox from intercepting arrow keys
        # and creating its own blue highlight. We manage selection ourselves.
        self._flow = Gtk.FlowBox()
        self._flow.set_valign(Gtk.Align.START)
        self._flow.set_max_children_per_line(100)
        self._flow.set_min_children_per_line(1)
        self._flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flow.set_homogeneous(True)
        self._flow.set_row_spacing(4)
        self._flow.set_column_spacing(4)
        self._scrolled.set_child(self._flow)

        # Status bar
        self._status_label = Gtk.Label(label="")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.set_margin_start(8)
        self._status_label.set_margin_bottom(4)
        self.append(self._status_label)

        # Keyboard controller
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        # Apply CSS
        self._apply_css()

    def _build_toolbar(self) -> Gtk.Box:
        """Build the top toolbar with sort selector and info."""
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)

        # Recursive toggle
        self._recursive_btn = Gtk.ToggleButton(label="📁 Recursive")
        self._recursive_btn.set_tooltip_text("Include subdirectories")
        self._recursive_btn.set_active(self._config.recursive)
        self._recursive_btn.connect("toggled", self._on_recursive_toggled)
        toolbar.append(self._recursive_btn)

        # Sort label
        sort_label = Gtk.Label(label="Sort:")
        toolbar.append(sort_label)

        # Sort dropdown
        self._sort_combo = Gtk.DropDown()
        sort_strings = Gtk.StringList()
        for name, desc in SORT_OPTIONS:
            sort_strings.append(f"{name} — {desc}")
        self._sort_combo.set_model(sort_strings)

        # Set current sort
        sort_names = [s[0] for s in SORT_OPTIONS]
        try:
            idx = sort_names.index(self._config.sort)
            self._sort_combo.set_selected(idx)
        except ValueError:
            pass

        self._sort_combo.connect("notify::selected", self._on_sort_changed)
        toolbar.append(self._sort_combo)

        # Thumbnail size slider
        size_label = Gtk.Label(label="Size:")
        toolbar.append(size_label)

        self._size_scale = Gtk.Scale()
        self._size_scale.set_digits(0)
        self._size_scale.set_range(50, 400)
        self._size_scale.set_value(self._config.thumbnail_size)
        self._size_scale.set_size_request(100, 24)
        self._size_scale.connect("value-changed", self._on_size_changed)
        toolbar.append(self._size_scale)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        # Rescan button
        rescan_btn = Gtk.Button(label="🔄 Rescan")
        rescan_btn.set_tooltip_text("Rescan directory and regenerate thumbnails")
        rescan_btn.connect("clicked", self._on_rescan_clicked)
        toolbar.append(rescan_btn)

        # Re-randomize button
        rerandom_btn = Gtk.Button(label="🎲 Re-randomize")
        rerandom_btn.set_tooltip_text("Shuffle images in random order")
        rerandom_btn.connect("clicked", self._on_rerandomize_clicked)
        toolbar.append(rerandom_btn)

        # Fullscreen toggle
        fullscreen_btn = Gtk.Button(label="⛶")
        fullscreen_btn.set_tooltip_text("Toggle fullscreen (F)")
        fullscreen_btn.connect("clicked", self._on_fullscreen_clicked)
        toolbar.append(fullscreen_btn)

        # Settings button
        settings_btn = Gtk.Button(label="⚙")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._on_settings_clicked)
        toolbar.append(settings_btn)

        # Slideshow button
        slideshow_btn = Gtk.Button(label="▶ Slideshow")
        slideshow_btn.connect("clicked", self._on_slideshow_clicked)
        toolbar.append(slideshow_btn)

        return toolbar

    def _on_rescan_clicked(self, btn: Gtk.Button) -> None:
        """Handle rescan button click - rescan directory and regenerate thumbnails."""
        self._window.rescan_and_reload()

    def _on_rerandomize_clicked(self, btn: Gtk.Button) -> None:
        """Handle re-randomize button click - shuffle images if in random mode."""
        # If current sort is random, re-sort to get a new random order
        if self._config.sort == "random":
            self._resort_images()
        else:
            # Temporarily switch to random sort
            self._config.sort = "random"
            sort_names = [s[0] for s in SORT_OPTIONS]
            try:
                idx = sort_names.index("random")
                self._sort_combo.set_selected(idx)
            except ValueError:
                pass
            self._resort_images()

    def _on_recursive_toggled(self, btn: Gtk.ToggleButton) -> None:
        """Handle recursive toggle change."""
        self._config.recursive = btn.get_active()
        # Save config
        self._window._save_config()

    def _on_size_changed(self, scale: Gtk.Scale) -> None:
        """Handle thumbnail size change - update display size in real-time."""
        new_size = int(scale.get_value())
        self._config.thumbnail_size = new_size
        # Update the picture size request for each tile - this should be enough
        # for GTK to naturally reflow without forcing column counts
        for tile in self._tiles:
            tile.set_size_request(new_size)
        # Just queue resize - no column calculation
        self._flow.queue_resize()
        # Save config (debounced by the slider)
        self._window._save_config()

    def _resort_images(self) -> None:
        """Re-sort images with the current sort mode and reload."""
        sorted_images = sort_images(self._images, self._config.sort)
        self.load_images(sorted_images)

    def _apply_css(self) -> None:
        """Apply CSS styling."""
        css = """
        .selected {
            background-color: alpha(@accent_color, 0.3);
            border: 2px solid @accent_color;
            border-radius: 4px;
        }
        .unviewed-border {
            border: 2px solid #4a90d9;
            border-radius: 4px;
        }
        .viewed {
            opacity: 0.75;
        }
        .unviewed-dot {
            color: #4a90d9;
            font-size: 14px;
        }
        .rating-stars {
            font-size: 11px;
            color: #f0a500;
        }
        .loading-label {
            font-size: 18px;
            margin: 8px 40px;
        }
        .loading-sub {
            font-size: 12px;
            opacity: 0.6;
            margin: 0 40px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def load_images(self, images: list[ImageInfo]) -> None:
        """Load a new list of images into the grid.
        
        If the images list is the same object as the current images,
        the grid is not rebuilt (thumbnails are already loaded).
        """
        # Skip rebuilding if images are the same object (no changes)
        if images is self._images and self._tiles:
            print(f"[DEBUG] load_images: same images object, skipping rebuild ({len(images)} images)")
            return
        
        print(f"[DEBUG] load_images: rebuilding grid with {len(images)} images")
        self._images = images
        self._selected_index = 0
        # Rebuild grid (called from idle in show_thumbnails)
        self._rebuild_grid()
        self._update_status()

    def _rebuild_grid(self) -> None:
        """Clear and rebuild the flow box with new tiles."""
        print(f"[DEBUG] _rebuild_grid called, creating {len(self._images)} tiles")
        # Remove all existing children
        child = self._flow.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self._flow.remove(child)
            child = next_child

        self._tiles = []

        for i, img in enumerate(self._images):
            tile = ThumbnailTile(
                img,
                self._config.thumbnail_size,
                self._config.highlight_color,
                self._config.unviewed_indicator,
            )
            tile.set_selected(i == self._selected_index)

            # Wrap in a FlowBoxChild
            fb_child = Gtk.FlowBoxChild()
            fb_child.set_child(tile)
            # Make FlowBoxChild non-focusable to prevent FlowBox from
            # intercepting arrow keys for focus navigation
            fb_child.set_can_focus(False)

            # Double-click gesture
            click = Gtk.GestureClick()
            click.connect("released", self._on_tile_clicked, i)
            fb_child.add_controller(click)

            # Note: No hover selection - must click to select

            self._flow.append(fb_child)
            self._tiles.append(tile)

        # Load thumbnails asynchronously
        self._load_thumbnails_async()

    def _load_thumbnails_async(self) -> None:
        """Load thumbnail images in a background thread."""
        self._loading_cancelled = False  # Reset cancellation flag
        images_to_load = list(enumerate(self._images))

        def _load():
            for i, img in images_to_load:
                # Check if loading was cancelled
                if self._loading_cancelled:
                    return
                thumb_path = get_or_create_thumbnail(
                    img.filepath,
                    img.file_modified,
                    self._config.thumbnail_size,
                )
                if self._loading_cancelled:
                    return
                if thumb_path:
                    GLib.idle_add(self._set_tile_thumbnail, i, thumb_path)
                else:
                    # Try loading directly with GdkPixbuf
                    GLib.idle_add(self._set_tile_pixbuf_from_file, i, img.filepath)

        thread = threading.Thread(target=_load, daemon=True)
        thread.start()

    def _set_tile_thumbnail(self, index: int, thumb_path: str) -> bool:
        """Set a tile's thumbnail from a cached file path (called on main thread)."""
        if index < len(self._tiles):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    thumb_path,
                    self._config.thumbnail_size,
                    self._config.thumbnail_size,
                    True,
                )
                self._tiles[index].set_pixbuf(pixbuf)
            except Exception:
                pass
        return False

    def _set_tile_pixbuf_from_file(self, index: int, filepath: str) -> bool:
        """Set a tile's thumbnail directly from the image file (fallback)."""
        if index < len(self._tiles):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    filepath,
                    self._config.thumbnail_size,
                    self._config.thumbnail_size,
                    True,
                )
                self._tiles[index].set_pixbuf(pixbuf)
            except Exception:
                pass
        return False

    def _update_status(self) -> None:
        """Update the status bar text."""
        total = len(self._images)
        viewed = sum(1 for img in self._images if img.viewed)
        unviewed = total - viewed
        if total > 0:
            sel = self._selected_index + 1
            img = self._images[self._selected_index]
            rating_str = _rating_stars(img.rating) if img.rating > 0 else "unrated"
            self._status_label.set_label(
                f"{sel}/{total} — {img.filename} — {rating_str} — "
                f"{viewed} viewed, {unviewed} unviewed"
            )
        else:
            self._status_label.set_label("No images found")

    def _select(self, index: int) -> None:
        """Select a tile by index."""
        if not self._tiles:
            return
        index = max(0, min(len(self._tiles) - 1, index))
        if self._selected_index < len(self._tiles):
            self._tiles[self._selected_index].set_selected(False)
        self._selected_index = index
        self._tiles[index].set_selected(True)
        self._update_status()
        # Scroll to make selected tile visible
        self._scroll_to_selected()

    def _scroll_to_selected(self) -> None:
        """Scroll the view to make the selected tile visible."""
        if self._selected_index < len(self._tiles):
            fb_child = self._flow.get_child_at_index(self._selected_index)
            if fb_child:
                # Don't use grab_focus() - FlowBox intercepts arrow keys for focus
                # navigation even with SelectionMode.NONE. Instead, scroll using
                # the ScrolledWindow's vadjustment.
                self._scroll_widget_into_view(fb_child)
    
    def _scroll_widget_into_view(self, widget: Gtk.Widget) -> None:
        """Scroll a widget into view using the ScrolledWindow's adjustment."""
        # Get the widget's allocation (position and size)
        allocation = widget.get_allocation()
        widget_y = allocation.y
        widget_height = allocation.height
        
        # Get the ScrolledWindow's vertical adjustment
        vadj = self._scrolled.get_vadjustment()
        if vadj is None:
            return
        
        # Current scroll position and visible height
        scroll_y = vadj.get_value()
        page_height = vadj.get_page_size()
        
        # Calculate if widget is out of view
        widget_bottom = widget_y + widget_height
        visible_bottom = scroll_y + page_height
        
        # Scroll down if widget is below visible area
        if widget_bottom > visible_bottom:
            new_value = widget_bottom - page_height
            vadj.set_value(new_value)
        # Scroll up if widget is above visible area
        elif widget_y < scroll_y:
            vadj.set_value(widget_y)

    def _on_tile_clicked(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float, index: int
    ) -> None:
        """Handle tile click: single click selects, double click opens slideshow."""
        self._select(index)
        if n_press >= 2:
            self._open_slideshow(index)

    def _on_tile_hover(
        self, controller: Gtk.EventControllerMotion, x: float, y: float, index: int
    ) -> None:
        """Handle mouse hover: select the hovered tile."""
        self._select(index)

    def _on_sort_changed(self, combo: Gtk.DropDown, _param) -> None:
        """Handle sort order change."""
        sort_names = [s[0] for s in SORT_OPTIONS]
        idx = combo.get_selected()
        if 0 <= idx < len(sort_names):
            new_sort = sort_names[idx]
            self._config.sort = new_sort
            sorted_images = sort_images(self._images, new_sort)
            self.load_images(sorted_images)
            # Save config
            self._window._save_config()

    def _on_slideshow_clicked(self, btn: Gtk.Button) -> None:
        """Start slideshow from current selection."""
        self._open_slideshow(self._selected_index)

    def _on_fullscreen_clicked(self, btn: Gtk.Button) -> None:
        """Toggle fullscreen mode."""
        if self._window.is_fullscreen():
            self._window.unfullscreen()
        else:
            self._window.fullscreen()

    def _on_settings_clicked(self, btn: Gtk.Button) -> None:
        """Show settings dialog."""
        self._window.show_settings()

    def _open_slideshow(self, start_index: int) -> None:
        """Open the slideshow view starting at the given index."""
        self._window.show_slideshow(self._images, start_index=start_index, auto_play=True)

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handle keyboard input."""
        # Calculate columns for grid navigation
        cols = max(1, self._get_columns())

        # Numpad navigation
        if keyval == Gdk.KEY_KP_8:  # Up
            self._select(self._selected_index - cols)
            return True
        elif keyval == Gdk.KEY_KP_2:  # Down
            self._select(self._selected_index + cols)
            return True
        elif keyval == Gdk.KEY_KP_4:  # Left
            self._select(self._selected_index - 1)
            return True
        elif keyval == Gdk.KEY_KP_6:  # Right
            self._select(self._selected_index + 1)
            return True

        # Arrow keys also work
        elif keyval == Gdk.KEY_Up:
            self._select(self._selected_index - cols)
            return True
        elif keyval == Gdk.KEY_Down:
            self._select(self._selected_index + cols)
            return True
        elif keyval == Gdk.KEY_Left:
            self._select(self._selected_index - 1)
            return True
        elif keyval == Gdk.KEY_Right:
            self._select(self._selected_index + 1)
            return True

        # Rating
        elif keyval in (Gdk.KEY_KP_Add, Gdk.KEY_plus):
            self._change_rating(+1)
            return True
        elif keyval in (Gdk.KEY_KP_Subtract, Gdk.KEY_minus):
            self._change_rating(-1)
            return True

        # Sort order cycling
        elif keyval in (Gdk.KEY_t, Gdk.KEY_T):
            self._cycle_sort()
            return True

        # Open slideshow
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._open_slideshow(self._selected_index)
            return True

        # Start slideshow from beginning of sorted list
        elif keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self._open_slideshow(0)
            return True

        # Quit
        elif keyval in (Gdk.KEY_q, Gdk.KEY_Q, Gdk.KEY_Escape):
            self._window.get_application().quit()
            return True

        # Delete selected image
        elif keyval == Gdk.KEY_Delete:
            self._delete_selected_image()
            return True

        # Help
        elif keyval in (Gdk.KEY_question, Gdk.KEY_F1):
            self._show_help()
            return True

        return False

    def handle_key(self, keyval: int) -> bool:
        """Public method to handle a key press from window-level controller."""
        return self._on_key_pressed(None, keyval, 0, Gdk.ModifierType(0))

    def cleanup(self) -> None:
        """Cancel background thumbnail loading. Call before switching views or closing."""
        self._loading_cancelled = True

    def _cycle_sort(self) -> None:
        """Cycle through sort options."""
        sort_names = [s[0] for s in SORT_OPTIONS]
        try:
            current_idx = sort_names.index(self._config.sort)
        except ValueError:
            current_idx = 0
        next_idx = (current_idx + 1) % len(sort_names)
        new_sort = sort_names[next_idx]
        self._config.sort = new_sort
        # Update dropdown
        self._sort_combo.set_selected(next_idx)
        sorted_images = sort_images(self._images, new_sort)
        self.load_images(sorted_images)

    def _show_help(self) -> None:
        """Show a brief help dialog."""
        help_text = """Navigation:
  Arrow keys / Numpad    Move selection

Actions:
  Enter / Space          Open slideshow at selected
  S                      Start slideshow from beginning
  T                      Cycle sort order
  Numpad +               Increase rating
  Numpad -               Decrease rating
  Delete                 Delete selected image from disk

Other:
  Q / Esc                Quit
  ? / F1                 This help"""
        
        # Create a simple dialog with the help text
        dialog = Gtk.Dialog()
        dialog.set_title("Keyboard Shortcuts")
        dialog.set_transient_for(self._window)
        dialog.set_modal(True)
        dialog.set_default_size(400, 300)
        
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

    def _change_rating(self, delta: int) -> None:
        """Change the rating of the currently selected image."""
        if not self._images or self._selected_index >= len(self._images):
            return
        img = self._images[self._selected_index]
        new_rating = self._app.update_rating(img.filepath, delta)
        if self._selected_index < len(self._tiles):
            self._tiles[self._selected_index].update_rating(new_rating)
        self._update_status()

    def _delete_selected_image(self) -> None:
        """Delete the currently selected image from disk after confirmation."""
        if not self._images or self._selected_index >= len(self._images):
            return
        
        img = self._images[self._selected_index]
        
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
            old_index = self._selected_index
            self._images.remove(img)
            self._app.images = self._images
            
            # Rebuild the grid
            self.load_images(self._images)
            
            # Adjust selection
            if self._selected_index >= len(self._images):
                self._selected_index = max(0, len(self._images) - 1)
            self._select(self._selected_index)
            
        except Exception as e:
            print(f"Error deleting image: {e}")

    def _get_columns(self) -> int:
        """Estimate the number of columns in the flow box."""
        width = self._flow.get_width()
        if width <= 0:
            return 1
        tile_width = self._config.thumbnail_size + 16  # tile + margins
        return max(1, width // tile_width)
