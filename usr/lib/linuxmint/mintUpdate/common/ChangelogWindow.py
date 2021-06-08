import requests

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk

from mintcommon import apt_changelog


class ChangelogWindow(Gtk.Window):
    """
    Retrieves the changelog and displays it in a new window.

    Parameters:

    `source` (str) can be a package name or a URL

    `name` (str) the name for the source to be used in the window title; if ommitted, `source` will be used

    `transient_for` (Gtk.Window) the window to be transient for

    `widget` (Gtk.Widget) will be set insensitive while the changelog window is showing

    `modal` (bool) controls whether the window will be a modal window

    """

    def __init__(self, source, name="", transient_for=None, widget=None, modal=False):
        super().__init__()
        self.source = source
        if name:
            self.source_name = name
        else:
            self.source_name = source
        self.transient_for = transient_for
        self.source_widget = widget
        if self.source_widget:
            self.source_widget.set_sensitive(False)
        self.modal = modal
        self.show_changelog()

    def __del__(self):
        if self.source_widget:
            self.source_widget.set_sensitive(True)

    def get_changelog(self):
        changelog = ""
        if "://" in self.source:
            try:
                r = requests.get(self.source)
                if r.ok:
                    r.encoding = None
                    changelog = r.text
                r.close()
            except:
                pass
        else:
            _apt_changelog = apt_changelog.AptChangelog()
            changelog = _apt_changelog.get_changelog(self.source)
        if not changelog:
            changelog = _("No changelog available")
        return changelog

    def show_changelog(self):
        self.set_transient_for(self.transient_for)
        self.set_modal(self.modal)
        self.set_default_size(-1, 500)
        self.set_icon_name("mintupdate")
        self.set_title(_("Changelog for %s") % self.source_name)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_border_width(12)
        box.set_hexpand(True)
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(True)
        scrolled_window.set_propagate_natural_width(True)
        scrolled_window.set_min_content_width(500)
        scrolled_window.set_size_request(-1, 500)
        output_box = Gtk.TextView()
        output_box.set_editable(False)
        output_box.set_right_margin(scrolled_window.get_vscrollbar().get_preferred_width().natural_width)
        output_box.set_wrap_mode(Gtk.WrapMode.WORD)
        output_box.set_monospace(True)
        scrolled_window.add(output_box)
        box.pack_start(scrolled_window, True, True, 0)
        self.add(box)
        changelog = self.get_changelog()
        self.show_all()
        changelog_buffer = output_box.get_buffer()
        def on_allocate(widget, _event):
            " Locks the container size in place after the first automatic resize "
            widget.disconnect(tw_allocate_handle)
            scrolled_window.set_size_request(
                widget.get_preferred_width().natural_width, 500)
            scrolled_window.set_propagate_natural_width(False)
            changelog_buffer.set_text(changelog)
        tw_allocate_handle = output_box.connect("size-allocate", on_allocate)
        changelog_buffer.set_text("x" * 80)
        self.connect("key-press-event", self.on_key_press_event)
        self.present_with_time(Gtk.get_current_event_time())

    @staticmethod
    def on_key_press_event(widget, event):
        if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            widget.destroy()
