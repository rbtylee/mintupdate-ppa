#!/usr/bin/python3

import subprocess

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

import setproctitle
from mintcommon.localization import localized_ui

from common import settings
from common.Logger import Logger
from kernel.KernelWindow import KernelWindow


class KernelControl:

    def __init__(self):
        setproctitle.setproctitle("kernel-manager")
        self.logger = Logger("kernel-manager")
        self.logger.write("Launching Kernel Manager")
        self.window = None
        self.app_hidden = True
        self.kernel_window_showing = True
        self.kernel_window = KernelWindow(self, True)
        self.preferences = Preferences(self.kernel_window)
        prefs_button = Gtk.Button.new()
        prefs_button.set_image(Gtk.Image.new_from_icon_name("document-properties-symbolic", Gtk.IconSize.BUTTON))
        prefs_button.set_tooltip_text(_("Preferences"))
        prefs_button.connect("clicked", self.show_preferences)
        prefs_button.show()
        top_row = self.kernel_window.builder.get_object("top_row")
        top_row.pack_start(prefs_button, False, False, 2)
        top_row.reorder_child(prefs_button, 0)
        self.cache_watcher = self
        self.window = self.kernel_window.window
        Gtk.main()

    def show_preferences(self, _widget):
        self.preferences.show()

    @staticmethod
    def open_help(*_args, **_kwargs):
        subprocess.Popen(["yelp", "help:mintupdate/index"])

    @staticmethod
    def set_sensitive(*_args, **_kwargs):
        pass

    @staticmethod
    def refresh(*_args, **_kwargs):
        pass

    @staticmethod
    def pause(*_args, **_kwargs):
        pass

    @staticmethod
    def get_window(*_args, **_kwargs):
        return None

class Preferences:

    def __init__(self, kernel_window):
        self.kernel_window = kernel_window
        self.refresh_required = False
        self.builder = Gtk.Builder.new_from_string(
            localized_ui("/usr/share/linuxmint/mintupdate/preferences.kernels.ui", _), -1)
        page_kernels = self.builder.get_object("page_kernels")
        button_box = Gtk.ButtonBox()
        close_button = Gtk.Button.new_with_label(_("Back"))
        close_button.connect("clicked", self.close)
        button_box.add(close_button)
        page_kernels.add(button_box)
        page_kernels.show_all()
        self.kernel_window.main_stack.add_named(page_kernels, "page_kernels")
        self.preferences_showing = False
        self.settings_handlers = {}
        self.connect_settings_handlers()

    def show(self):
        self.preferences_showing = True
        # Initialize
        # (this is adapted from mintupdate's main/Preferences.py)
        use_mainline_kernels_widget = self.initialize_widget("use-mainline-kernels", self.on_use_mainline_toggle)
        self.initialize_widget("mainline-include-rc")
        self.initialize_widget("mainline-include-longterm")
        self.initialize_widget("allow-kernel-type-selection")
        self.on_use_mainline_toggle(use_mainline_kernels_widget)
        # This one we don't need in the stand-alone version
        self.builder.get_object("mainline-upgrade-eol-series").hide()
        # Show preferences on the stack
        self.kernel_window.main_stack.set_visible_child_name("page_kernels")

    def connect_settings_handlers(self):
        for name in ("use-mainline-kernels",
                     "mainline-include-rc",
                     "mainline-include-longterm",
                     "allow-kernel-type-selection"):
            self.settings_handlers[name] = settings.connect(f"changed::{name}", self.update_state)

    def update_state(self, _settings, name):
        self.refresh_required = True
        if self.preferences_showing:
            widget = self.builder.get_object(name)
            widget.handler_block_by_func(self.on_setting_toggled)
            widget.set_active(settings.get_boolean(name))
            widget.handler_unblock_by_func(self.on_setting_toggled)

    def on_use_mainline_toggle(self, widget):
        self.builder.get_object("mainline_options").set_visible(widget.get_active())

    def close(self, _widget):
        self.preferences_showing = False
        if self.refresh_required:
            self.refresh_required = False
            self.kernel_window.refresh_kernels_list()
        else:
            self.kernel_window.main_stack.set_visible_child_name("main_box")

    def initialize_widget(self, name, additional_callback=None):
        widget = self.builder.get_object(name)
        # Set value
        event = "toggled"
        widget.set_active(settings.get_boolean(name))
        # disconnect handlers because we're being lazy and run this function on every show
        try:
            widget.disconnect_by_func(self.on_setting_toggled)
            if additional_callback:
                widget.disconnect_by_func(additional_callback)
        except TypeError:
            pass
        # Connect callbacks
        widget.connect(event, self.on_setting_toggled, name)
        if additional_callback:
            widget.connect(event, additional_callback)
        return widget

    def on_setting_toggled(self, widget, name):
        settings.handler_block(self.settings_handlers[name])
        settings.set_boolean(name, widget.get_active())
        settings.handler_unblock(self.settings_handlers[name])
        self.refresh_required = True

if __name__ == "__main__":
    KernelControl()
