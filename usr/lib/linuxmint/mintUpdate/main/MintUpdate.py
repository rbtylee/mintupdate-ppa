import configparser
import os
import subprocess
import sys
import traceback

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gdk, Gtk, Notify, GLib, GObject

import psutil
from mintcommon import apt_changelog
from mintcommon.localization import localized_ui

from common import settings
from common.constants import PKEXEC_ENV, ROOT_FUNCTIONS
from common.dialogs import show_confirmation_dialog, show_dpkg_lock_msg
from common.functions import dpkg_locked
from common.Logger import Logger
from kernel.KernelWindow import KernelWindow
from main.AutomaticRefreshThread import AutomaticRefreshThread
from main.CacheWatcher import CacheWatcher
from main.ChangelogRetrieverThread import ChangelogRetrieverThread
from main.constants import (UPDATE_CHECKED, UPDATE_DISPLAY_NAME,
                            UPDATE_NEW_VERSION, UPDATE_OBJ, UPDATE_OLD_VERSION,
                            UPDATE_SIZE, UPDATE_SIZE_STR, UPDATE_SOURCE,
                            UPDATE_TOOLTIP, UPDATE_TYPE, UPDATE_TYPE_PIX)
from main.functions import check_export_blacklist, size_to_string
from main.HistoryWindow import HistoryWindow
from main.Infobars import Infobars
from main.InstallThread import InstallThread
from main.LogView import LogView
from main.PipeMonitor import PipeMonitor
from main.Preferences import Preferences
from main.RefreshThread import RefreshThread

# AppIndicator
try:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3 as AppIndicator
    HAVE_APP_INDICATOR = True
except ImportError:
    try:
        from gi.repository import AppIndicator
        HAVE_APP_INDICATOR = True
    except ImportError:
        HAVE_APP_INDICATOR = False
except:
    HAVE_APP_INDICATOR = False


class MintUpdate:
    """ The Linux Mint Update Manager """

    def __init__(self):
        self.HAVE_APP_INDICATOR = HAVE_APP_INDICATOR
        self.app_hidden = True
        self.information_window_showing = False
        self.history_window_showing = False
        self.preferences_window = None
        self.kernel_window_showing = False
        self.kernel_window = None
        self.refresh_inhibited = False
        self.reboot_required = False
        self.refreshing = False
        self.changelog_retriever_started = False
        self.logger = Logger("mintupdate")
        self.logger.write("Launching Update Manager")

        # add monospace style definition as a fallback for legacy themes like Mint-X that lack it
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(".monospace {font-family: Monospace;}".encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_FALLBACK)
        del css_provider

        self.builder = Gtk.Builder.new_from_string(
            localized_ui("/usr/share/linuxmint/mintupdate/main.ui", _), -1)

        # Main window
        self.window = self.builder.get_object("main_window")
        self.window.connect("key-press-event", self.on_key_press_event)
        self.window.connect("window-state-event", self.on_window_state_event)
        self.stack = Gtk.Stack()
        self.builder.get_object("stack_container").pack_start(self.stack, True, True, 0)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(175)
        self.statusbar = self.builder.get_object("statusbar")
        self.context_id = self.statusbar.get_context_id("mintUpdate")

        # libnotify init
        self.show_notifications = settings.get_boolean("show-desktop-notifications")
        if self.show_notifications:
            Notify.init("mintUpdate")

        try:
            self.accel_group = Gtk.AccelGroup()
            self.window.add_accel_group(self.accel_group)

            self.toolbar = self.builder.get_object("toolbar1")
            self.notebook_details = self.builder.get_object("notebook_details")
            self.textview_packages = self.builder.get_object("textview_packages").get_buffer()
            self.textview_description = self.builder.get_object("textview_description").get_buffer()
            self.textview_changes = self.builder.get_object("textview_changes").get_buffer()
            self.paned = self.builder.get_object("paned")

            # Updates page
            self.stack.add_named(self.builder.get_object("updates_page"), "updates_available")

            # initialize the Infobars object
            self.infobars = Infobars(self)

            # Updates treeview
            self.treeview = self.builder.get_object("treeview_update")
            self.builder.get_object("scrolled_updates").set_overlay_scrolling(False)
            self.treeview.set_tooltip_column(UPDATE_TOOLTIP)

            column_type = self.add_treeview_column(
                _("Type"), UPDATE_TYPE, Gtk.CellRendererPixbuf(), icon_name=UPDATE_TYPE_PIX)
            column_type.set_resizable(False)
            cr = Gtk.CellRendererToggle()
            cr.connect("toggled", self.toggled)
            column_upgrade = self.add_treeview_column("", UPDATE_CHECKED, cr)
            column_upgrade.set_resizable(False)
            column_upgrade.set_cell_data_func(cr, self.celldatafunction_checkbox)
            column_name = self.add_treeview_column(
                _("Name"), UPDATE_DISPLAY_NAME, Gtk.CellRendererText(), markup=UPDATE_DISPLAY_NAME)
            column_old_version = self.add_treeview_column(
                _("Old Version"), UPDATE_OLD_VERSION, Gtk.CellRendererText(), text=UPDATE_OLD_VERSION)
            column_new_version = self.add_treeview_column(
                _("New Version"), UPDATE_NEW_VERSION, Gtk.CellRendererText(), text=UPDATE_NEW_VERSION)
            column_size = self.add_treeview_column(
                _("Size"), UPDATE_SIZE, Gtk.CellRendererText(), text=UPDATE_SIZE_STR)
            column_origin = self.add_treeview_column(
                _("Origin"), UPDATE_SOURCE, Gtk.CellRendererText(), text=UPDATE_SOURCE)

            self.treeview.append_column(column_upgrade)
            self.treeview.append_column(column_type)
            self.treeview.append_column(column_name)
            self.treeview.append_column(column_old_version)
            self.treeview.append_column(column_new_version)
            self.treeview.append_column(column_origin)
            self.treeview.append_column(column_size)

            self.treeview.connect("button-release-event", self.treeview_right_clicked)
            self.treeview.connect("row-activated", self.treeview_row_activated)
            self.treeview.get_selection().connect("changed", self.display_selected_package)

            self.treeview.show()

            self.notebook_details.connect("switch-page", self.switch_page)
            self.window.connect("delete_event", self.close_window)

            # Toolbar buttons
            self.tool_apply = self.configure_toolbar_button("tool_apply", self.install, "<Control>I")
            self.tool_clear = self.configure_toolbar_button("tool_clear", self.clear, "<Control><Shift>A")
            self.tool_select_all = self.configure_toolbar_button("tool_select_all", self.select_updates, "<Control>A")
            self.tool_refresh = self.configure_toolbar_button("tool_refresh", self.force_refresh, "<Control>R")

            # Self-update page:
            self.builder.get_object("confirm-self-update").connect("clicked", self.self_update, self.builder.get_object("automatic-self-update"))
            self.builder.get_object("automatic-self-update").connect("clicked", self.automatic_self_update, self.builder.get_object("confirm-self-update"))

            # Refreshing page spinner:
            self.status_refreshing_spinner = self.builder.get_object("status_refreshing_spinner")

            self.tray_menu = Gtk.Menu()
            self.toggle_menu_item = self.add_menu_item(self.tray_menu, _("Show"), "view-restore-symbolic", self.on_statusicon_clicked)
            self.tray_menu_items = []
            self.tray_menu_refresh = self.add_menu_item(
                self.tray_menu, _("Refresh"), "view-refresh-symbolic", self.force_refresh)
            self.tray_menu_items.append(self.tray_menu_refresh)
            self.tray_menu_kernels = self.add_menu_item(
                self.tray_menu, _("Kernel Manager"), "kernel-manager", self.open_kernels)
            self.tray_menu_items.append(self.tray_menu_kernels)
            self.add_menu_item(
                self.tray_menu, _("History of Updates"), "document-open-recent-symbolic", self.open_history)
            self.tray_menu_preferences = self.add_menu_item(
                self.tray_menu, _("Preferences"), "preferences-other-symbolic", self.open_preferences)
            self.tray_menu_items.append(self.tray_menu_preferences)
            self.add_menu_item(self.tray_menu, _("Log View"), "dialog-information-symbolic", self.open_information)
            self.tray_menu_quit = self.add_menu_item(
                self.tray_menu, _("Quit"), "application-exit-symbolic", self.quit)
            self.tray_menu.show_all()

            # System tray status icon
            self.status_icon = None
            settings.connect("changed::use-appindicator", self.set_use_appindicator)
            self.use_appindicator = False
            self.set_use_appindicator(settings, "use-appindicator")

            # File menu
            self.file_menu = Gtk.MenuItem.new_with_mnemonic(_("_File"))
            file_submenu = Gtk.Menu()
            self.file_menu.set_submenu(file_submenu)
            self.add_menu_item(file_submenu, _("Hide"), "window-close-symbolic", self.hide_main_window, "<Control>W")
            self.add_menu_item(file_submenu, _("Quit"), "application-exit-symbolic", self.quit)

            # Edit menu
            edit_menu = Gtk.MenuItem.new_with_mnemonic(_("_Edit"))
            edit_submenu = Gtk.Menu()
            edit_menu.set_submenu(edit_submenu)
            self.add_menu_item(edit_submenu, _("Preferences"), "preferences-other-symbolic", self.open_preferences)
            if os.path.exists("/usr/bin/software-sources") or os.path.exists("/usr/bin/software-properties-gtk"):
                self.add_menu_item(edit_submenu, _("Software Sources"),
                    "system-software-install-symbolic", self.open_repositories)
            if os.path.exists("/usr/bin/timeshift-gtk"):
                self.add_menu_item(edit_submenu, _("System Snapshots"),
                    "document-open-recent-symbolic", self.open_timeshift)

            # Check if new Linux Mint point release is available
            self.notify_release_upgrade = None
            rel_edition = 'unknown'
            rel_codename = 'unknown'
            path = "/etc/linuxmint/info"
            if os.path.exists(path):
                with open(path) as f:
                    mintinfo = f.read()
                config = configparser.ConfigParser()
                config.read_string(f'[general]\n{mintinfo}')
                try:
                    rel_edition = config["general"]["EDITION"].strip('"')
                    rel_codename = config["general"]["CODENAME"].strip('"')
                except:
                    pass
                del config
            path = f"/usr/share/mint-upgrade-info/{rel_codename}/info"
            if os.path.exists(path):
                config = configparser.ConfigParser()
                config.read(path)
                if rel_edition.lower() in config['general']['editions']:
                    rel_target = config['general']['target_name']
                    self.add_menu_item(edit_submenu, _("Upgrade to %s") % rel_target,
                        "mintupdate-type-package-symbolic", self.open_rel_upgrade)
                    if settings.get_string("release-upgrade-notified") != rel_codename:
                        self.notify_release_upgrade = (rel_codename, rel_target)
                del config
            del rel_edition
            del rel_codename
            del path

            # Visible columns sub menu
            visible_columns_menu = Gtk.Menu()
            self.add_menu_item(visible_columns_menu, _("Type"), column_type, "show-type-column")
            self.add_menu_item(visible_columns_menu, _("Package"), column_name, "show-package-column")
            self.add_menu_item(visible_columns_menu, _("Old Version"), column_old_version, "show-old-version-column")
            self.add_menu_item(visible_columns_menu, _("New Version"), column_new_version, "show-new-version-column")
            self.add_menu_item(visible_columns_menu, _("Origin"), column_origin, "show-origin-column")
            self.add_menu_item(visible_columns_menu, _("Size"), column_size, "show-size-column")

            # View menu
            viewMenu = Gtk.MenuItem.new_with_mnemonic(_("_View"))
            viewSubmenu = Gtk.Menu()
            viewMenu.set_submenu(viewSubmenu)
            self.add_menu_item(viewSubmenu, _("Visible Columns"), "dialog-information-symbolic", visible_columns_menu)
            self.show_descriptions_menu_item = self.add_menu_item(viewSubmenu, _("Show Descriptions"),
                settings.get_boolean("show-descriptions"), self.setVisibleDescriptions)

            # Tools menu
            tools_menu = Gtk.MenuItem.new_with_mnemonic(_("_Tools"))
            tools_submenu = Gtk.Menu()
            tools_menu.set_submenu(tools_submenu)
            # Only support kernel selection in Linux Mint (not LMDE)
            self.add_menu_item(tools_submenu,
                _("Kernel Manager"), "kernel-manager", self.open_kernels, "<Control><Shift>K")
            self.add_menu_item(tools_submenu,
                _("History of Updates"), "document-open-recent-symbolic", self.open_history, "<Control><Shift>H")
            self.add_menu_item(tools_submenu,
                _("Log View"), "dialog-information-symbolic", self.open_information, "<Control><Shift>L")

            # Help menu
            helpMenu = Gtk.MenuItem.new_with_mnemonic(_("_Help"))
            helpSubmenu = Gtk.Menu()
            helpMenu.set_submenu(helpSubmenu)
            self.add_menu_item(helpSubmenu, _("Contents"), "help-contents-symbolic", self.open_help, "F1")
            if Gtk.check_version(3,20,0) is None:
                self.add_menu_item(helpSubmenu, _("Keyboard Shortcuts"),
                    "preferences-desktop-keyboard-shortcuts-symbolic", self.open_shortcuts)
            self.add_menu_item(helpSubmenu, _("About"), "help-about-symbolic", self.open_about)

            # Main window menu
            self.menubar = self.builder.get_object("menubar1")
            self.menubar.append(self.file_menu)
            self.menubar.append(edit_menu)
            self.menubar.append(viewMenu)
            self.menubar.append(tools_menu)
            self.menubar.append(helpMenu)
            self.menubar.show_all()

            # Status pages
            self.stack.add_named(self.builder.get_object("status_updated"), "status_updated")
            self.stack.add_named(self.builder.get_object("status_error"), "status_error")
            self.stack.add_named(self.builder.get_object("status_self-update"), "status_self-update")
            self.stack.add_named(self.builder.get_object("status_refreshing"), "status_refreshing")
            self.show_page("status_refreshing")
            self.stack.show_all()

            # Start thread to monitor named pipe in case the application gets started a second time
            self.pipe_monitor = PipeMonitor(self)
            self.pipe_monitor.start()

            # We hardcode the initial `show` here because we only just initialized the pipe,
            if len(sys.argv) > 1 and sys.argv[1] == "show":
                self.show_window()

            self.cache_watcher = CacheWatcher(self)
            self.cache_watcher.start()

            self.window_restore_settings()
            self.refresh_schedule_enabled = settings.get_boolean("refresh-schedule-enabled")
            self.auto_refresh = None
            self.restart_auto_refresh()

            # Cleanup
            del column_type
            del column_name
            del column_old_version
            del column_new_version
            del column_origin
            del column_size
            del file_submenu
            del edit_menu
            del edit_submenu
            del viewMenu
            del viewSubmenu
            del tools_menu
            del tools_submenu
            del helpMenu
            del helpSubmenu

            # Start main lopp
            Gtk.main()
        except:
            traceback.print_exc()
            self.logger.write_error(f"Exception occurred in main thread:\n{traceback.format_exc()}")
            self.quit()

######### KEYBINDS #########

    def on_key_press_event(self, _widget, event):
        modifiers = (Gdk.ModifierType.SHIFT_MASK |
                     Gdk.ModifierType.CONTROL_MASK |
                     Gdk.ModifierType.MOD1_MASK |
                     Gdk.ModifierType.SUPER_MASK |
                     Gdk.ModifierType.HYPER_MASK |
                     Gdk.ModifierType.META_MASK)
        if (event.state & Gdk.ModifierType.CONTROL_MASK) and not (event.state &
            (modifiers ^ (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK))):
            if event.keyval == Gdk.KEY_L:
                self.open_information(None)
                # Return True here because we also have an accelerator defined
                # and don't want to run this twice. Duplicating the bind here
                # so it works even while the menu item is insensitive, and we
                # need to keep the accelerator to display the bind in the menu
                return True
            elif event.keyval == Gdk.KEY_s:
                self.select_updates(security=True)
            elif event.keyval == Gdk.KEY_k:
                self.select_updates(kernel=True)
        return False

######### UTILITY FUNCTIONS #########

    def show_notification(self, summary, body, icon="mintupdate", show_button=False, urgency=Notify.Urgency.NORMAL):
        """
        Show desktop notification, if self.show_notifications is set.

        parameters:
        `summary` - notification title
        `body` - notification text
        `icon` - notification icon, defaults to mintupdate
        `show_button` - set to show a button in the notification to show the main window.
        `urgency` - set notification urgency, defaults to NORMAL
        """
        # Only show notifications when the window is hidden or minimized:
        # if self.show_notifications and (self.app_hidden or
        #    (self.window.get_window().get_state() & Gdk.WindowState.ICONIFIED)):

        # Not sure I like it, let's keep showing them regardless for now:
        if self.show_notifications:
            notification = Notify.Notification.new(summary, body, icon)
            if show_button:
                notification.add_action(
                    "show_window",
                    "Show Update Manager",
                    self.show_window, # callback
                    notification # we need to keep a reference to the
                                 # notification object for the callback to work
                )
            notification.set_urgency(urgency)
            notification.show()

    def show_page(self, page):
        """ Shows `page` on self.stack, with `page` being either a page object or name """
        if isinstance(page, str):
            self.stack.set_visible_child_name(page)
            self.statusbar.set_opacity(100 * (page == "updates_available"))
            self.statusbar.set_visible(100 * (page == "updates_available"))
        else:
            self.stack.set_visible_child(page)

    def set_sensitive(self, status, allow_quit=True, set_toolbar_buttons=False):
        """
        Wrapper for the main application, selectively toggles sensitivity of
        self.treeview, self.menubar, and depending on `set_toolbar_buttons` of
        either self.toolbar or the individual toolbar buttons.
        Also runs self.tray_menu_set_sensitive with the `allow_quit` parameter.
        """
        self.treeview.set_sensitive(status)
        for child in self.menubar.get_children():
            child.set_sensitive(status)
        if set_toolbar_buttons:
            for child in self.toolbar.get_children():
                child.set_sensitive(status)
        else:
            self.toolbar.set_sensitive(status)
        self.tray_menu_set_sensitive(status, allow_quit)

    def restart_auto_refresh(self):
        """ Starts AutomaticRefreshThread if self.auto_refresh is not set """
        if self.auto_refresh:
            return
        self.auto_refresh = AutomaticRefreshThread(self)
        self.auto_refresh.start()

    def refresh(self, root_mode=False):
        """
        Starts RefreshThread if not `self.refreshing` or `self.updates_inhibited`.
        Returns a reference to the running `RefreshThread`.
        """
        if self.refreshing:
            self.logger.write("Additional refresh request ignored because of ongoing refresh")
            return False
        refresh = RefreshThread(self, root_mode=root_mode)
        refresh.start()
        return refresh

    def set_status_message(self, message):
        """ Pushes `message` to the main window's statusbar """
        self.statusbar.push(self.context_id, message)

    def set_status_message_selected(self):
        """
        Sets the status message according to the number of selected updates.
        Also toggles self.tool_apply sensitivity accordingly.
        """
        model = self.treeview.get_model()
        if not len(model):
            return
        download_size = 0
        num_selected = 0
        for row in model:
            if row[UPDATE_CHECKED] == "true":
                size = row[UPDATE_SIZE]
                download_size += size
                num_selected += 1
        if num_selected == 0:
            self.tool_apply.set_sensitive(False)
            statusString = _("No updates selected")
        else:
            self.tool_apply.set_sensitive(True)
            statusString = ngettext("%(selected)d update selected (%(size)s)",
                                    "%(selected)d updates selected (%(size)s)", num_selected) % \
                                    {'selected':num_selected, 'size':size_to_string(download_size)}
        self.set_status_message(statusString)

    def set_status(self, icon, message):
        """ Set statusbar message and tray icon tooltip and icon (thread-safe) """
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.set_status_message, message)
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.set_status_icon, icon, message)

######### MAIN WINDOW FUNCTIONS ##########

    def on_window_state_event(self, _widget, event):
        """ Window state event handler """
        if event.new_window_state & (Gdk.WindowState.ICONIFIED | Gdk.WindowState.WITHDRAWN):
            self.toggle_hidden(True)
        else:
            self.toggle_hidden(False)

    def hide_main_window(self, _widget=None):
        """ Hide the main window and call `apt_changelog.drop_cache()` """
        self.window.hide()
        if self.kernel_window_showing and self.kernel_window:
            self.kernel_window.window.hide()
        elif self.preferences_window:
            self.preferences_window.window.hide()
        apt_changelog.drop_cache()

    def close_window(self, window, _event=None):
        """ Macro for `self.window_save_settings()` and `self.hide_main_window()` """
        self.window_save_settings()
        self.hide_main_window(window)
        return True

    def show_window(self, *_args, **_kwargs):
        """ Present main or kernel window """
        timestamp = Gtk.get_current_event_time()
        self.window.show()
        if self.kernel_window_showing and self.kernel_window:
            self.kernel_window.window.set_skip_taskbar_hint(True)
            self.kernel_window.window.present_with_time(timestamp)
        elif self.preferences_window:
            self.preferences_window.window.set_skip_taskbar_hint(True)
            self.preferences_window.window.present_with_time(timestamp)
        else:
            # The only time we do not have a timestamp is on application launch
            # We could get one like this but shouldn't be necessary:
            # if not timestamp:
            #     timestamp = GdkX11.x11_get_server_time(self.window.get_window())
            if timestamp:
                self.window.present_with_time(timestamp)
            else:
                # this probably does nothing
                self.window.present()

    def window_restore_settings(self):
        self.window.resize(settings.get_uint("window-width"),
                           settings.get_uint("window-height"))
        # Try to set the window position (won't work on wayland display servers)
        x, y = settings.get_value("window-position").unpack()
        self.window.move(max(0, x), y)
        GObject.Value.unset(x)
        GObject.Value.unset(y)
        self.paned.set_position(settings.get_uint('window-pane-position'))

    def window_save_settings(self):
        settings.set_uint("window-width", self.window.get_size()[0])
        settings.set_uint("window-height", self.window.get_size()[1])
        settings.set_uint("window-pane-position", self.paned.get_position())
        position = self.window.get_position()
        settings.set_value("window-position",
            GLib.Variant.new_tuple(
                GLib.Variant.new_int32(position.root_x),
                GLib.Variant.new_int32(position.root_y)
            ))
        model = self.treeview.get_model()
        if model:
            sort_column_id, sort_order = model.get_sort_column_id()
            settings.set_int("sort-column-id", int(sort_column_id))
            settings.set_int("sort-order", int(sort_order))

    def quit(self, _widget=None):
        self.window_save_settings()
        self.logger.write("Exiting - requested by user")
        self.logger.close()
        self.pipe_monitor.stop()
        del self.pipe_monitor
        Notify.uninit()
        Gtk.main_quit()

######### MENU/TOOLBAR FUNCTIONS #########

    def add_menu_item(self, menu, label, data, callback, keybind=None):
        """
        Handles all menu items for both the main window and the status icon:

        `menu` - the menu to append to
        `label` - menu label
        `data` - one of icon name, bool or Gtk.TreeViewColumn depending on menu item type
        `callback` - one of a function reference, a Gtk.Menu item or a dconf key name
        `keybind` - optional string of the key combination to bind to

        Returns the menu item with the event handler id in the `handler` parameter
        """
        handler = None
        if isinstance(data, str):
            menu_item = Gtk.ImageMenuItem()
            menu_item.set_image(Gtk.Image.new_from_icon_name(data, Gtk.IconSize.MENU))
            if isinstance(callback, Gtk.Menu):
                menu_item.set_submenu(callback)
            else:
                handler = menu_item.connect("activate", callback)
        else:
            menu_item = Gtk.CheckMenuItem()
            if isinstance(data, Gtk.TreeViewColumn):
                toggled = settings.get_boolean(callback)
                menu_item.set_active(toggled)
                data.set_visible(toggled)
                handler = menu_item.connect("toggled", self.setVisibleColumn, data, callback)
            else:
                menu_item.set_active(data)
                handler = menu_item.connect("toggled", callback)
        menu_item.set_label(label)
        if keybind:
            key, mod = Gtk.accelerator_parse(keybind)
            menu_item.add_accelerator("activate", self.accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        menu_item.handler = handler
        menu.append(menu_item)
        return menu_item

    def configure_toolbar_button(self, button_id, callback, keybind):
        button = self.builder.get_object(button_id)
        button.connect("clicked", callback)
        key, mod = Gtk.accelerator_parse(keybind)
        button.add_accelerator("clicked", self.accel_group, key, mod, Gtk.AccelFlags.VISIBLE)
        return button

    @staticmethod
    def setVisibleColumn(checkmenuitem, column, key):
        state = checkmenuitem.get_active()
        settings.set_boolean(key, state)
        column.set_visible(state)

    def setVisibleDescriptions(self, checkmenuitem):
        settings.set_boolean("show-descriptions", checkmenuitem.get_active())
        self.refresh()

    def clear(self, _widget):
        self.tool_apply.set_sensitive(False)
        model = self.treeview.get_model()
        for row in model:
            row[0] = "false"
        if len(model):
            self.set_status_message(_("No updates selected"))

    def select_updates(self, _widget=None, security=False, kernel=False):
        """ Set UPDATE_CHECKED in update rows, optionally depending on update type """
        model = self.treeview.get_model()
        if not model:
            return
        for row in model:
            update =  row[UPDATE_OBJ]
            if security:
                if update.type == "security":
                    row[UPDATE_CHECKED] = "true"
            elif kernel:
                if update.type == "kernel":
                    row[UPDATE_CHECKED] = "true"
            else:
                row[UPDATE_CHECKED] = "true"
        self.set_status_message_selected()

    def force_refresh(self, _widget):
        """ Manual refresh (button and tray menu) callback, calls self.refresh if not dpkg_locked() """
        if dpkg_locked():
            show_dpkg_lock_msg(self.window)
        else:
            self.refresh(root_mode=True)

    def install(self, _widget=None):
        """ Starts InstallThread if not dpkg_locked() """
        if dpkg_locked():
            show_dpkg_lock_msg(self.window)
            return False
        InstallThread(self).start()
        return True

######### SELF-UPDATE PAGE FUNCTIONS #######

    def self_update(self, button, checkbutton):
        """ Callback for the confirm-self-update button, also gets called by self.automatic_self_update() """
        self.select_updates()
        if self.install():
            button.set_sensitive(False)
            checkbutton.set_sensitive(False)

    def automatic_self_update(self, checkbutton, button):
        """ Callback for the automatic-self-update checkbox. Calls self.self_update() """
        toggled = checkbutton.get_active()
        settings.set_boolean("automatic-self-update", toggled)
        if toggled:
            self.self_update(button, checkbutton)

    def show_self_update_changelog(self, event, CHANGELOG_HEIGHT, changelog):
        """
        Shows the self-update changelog and in particular resizes the container
        to 80 char width.

        This is called directly or set up as a callback by RefreshThread, where
        it cannot be done directly due to the automatic sizing not working
        correctly while the window is hidden, so we wait for it to show.
        """
        if event:
            self.window.disconnect_by_func(self.show_self_update_changelog)
        changelog_container = self.builder.get_object("changelog_self-update")
        changelog_container.set_propagate_natural_width(True)
        changelog_container.set_size_request(-1, CHANGELOG_HEIGHT)
        textview = self.builder.get_object("textview_self-update")
        textview.set_right_margin(
            changelog_container.get_vscrollbar().get_preferred_width().natural_width)
        changelog_buffer = textview.get_buffer()
        def on_allocate(widget, _event, changelog):
            " Locks the container size in place after the first automatic resize "
            widget.disconnect(tw_allocate_handle)
            changelog_container.set_size_request(
                widget.get_preferred_width().natural_width,
                CHANGELOG_HEIGHT)
            changelog_container.set_propagate_natural_width(False)
            changelog_buffer.set_text(changelog)
        tw_allocate_handle = textview.connect("size-allocate", on_allocate, changelog)
        changelog_buffer.set_text("x" * 80)

######### TREEVIEW/SELECTION FUNCTIONS #######

    @staticmethod
    def add_treeview_column(label, sort, cellrenderer, **kwargs):
        column = Gtk.TreeViewColumn(label, cellrenderer, **kwargs)
        column.set_sort_column_id(sort)
        column.set_resizable(True)
        return column

    @staticmethod
    def celldatafunction_checkbox(_column, cell, model, tree_iter, _data):
        cell.set_property("activatable", True)
        if model.get_value(tree_iter, UPDATE_CHECKED) == "true":
            cell.set_property("active", True)
        else:
            cell.set_property("active", False)

    def treeview_row_activated(self, _treeview, path, _view_column):
        self.toggled(None, path)

    def toggled(self, _renderer, path):
        model = self.treeview.get_model()
        if model[path][UPDATE_CHECKED] == "true":
            model[path][UPDATE_CHECKED] = "false"
        else:
            model[path][UPDATE_CHECKED] = "true"
        self.set_status_message_selected()

    def display_selected_package(self, selection):
        if not self.notebook_details.get_visible():
            self.notebook_details.show()
        try:
            self.textview_packages.set_text("")
            self.textview_description.set_text("")
            self.textview_changes.set_text("")
            model, tree_iter = selection.get_selected()
            if tree_iter:
                package_update = model.get_value(tree_iter, UPDATE_OBJ)
                if self.notebook_details.get_current_page() == 2:
                    self.display_package_changelog(package_update)
                self.display_package_list(package_update)
                self.display_package_description(package_update)
        except:
            self.logger.write_error(f"Exception showing update details:\n{traceback.format_exc()}")

    def display_package_changelog(self, package_update):
        if not hasattr(package_update, "retrieving_changelog"):
            if package_update.changelog:
                self.display_changelog(package_update.changelog)
                GObject.Value.unset(package_update)
            else:
                self.display_changelog(_("Downloading changelog…"))
                ChangelogRetrieverThread(package_update, self.treeview, self.display_changelog).start()

    def display_changelog(self, changelog):
        self.textview_changes.set_text(changelog)
        while Gtk.events_pending():
            Gtk.main_iteration()

    def switch_page(self, _notebook, _page, page_num):
        model, tree_iter = self.treeview.get_selection().get_selected()
        if tree_iter and page_num == 2:
            package_update = model.get_value(tree_iter, UPDATE_OBJ)
            self.display_package_changelog(package_update)

    def display_package_list(self, package_update):
        prefix = "\n    • "
        count = len(package_update.package_names)
        if package_update.origin == "ubuntu" and package_update.archive.startswith("mainline"):
            package_list = [name.split("_")[0] for name in package_update.package_names]
        else:
            package_list = package_update.package_names
        installed_size_string = size_to_string(package_update.installed_size)
        if package_update.installed_size_change:
            # size_to_string() doesn't support negative numbers so we flip a negative sign here
            if package_update.installed_size_change < 0:
                package_update.installed_size_change *= -1
                sign = "-"
            else:
                sign = "+"
            installed_size_string += f" ({sign}{size_to_string(package_update.installed_size_change)})"
        packages = "%s%s%s\n%s\n%s\n" % \
            (ngettext("This update affects the following installed package:",
                      "This update affects the following installed packages:",
                      count),
             prefix,
             prefix.join(sorted(package_list)),
             _("Download size: %s") % size_to_string(package_update.size),
             _("Installed size: %s") % installed_size_string)
        self.textview_packages.set_text(packages)

    def display_package_description(self, package_update):
        self.textview_description.set_text(package_update.description.replace("\\n", "\n"))

    def treeview_right_clicked(self, widget, event):
        if event.button == 3:
            model, tree_iter = widget.get_selection().get_selected()
            if tree_iter:
                package_update = model.get_value(tree_iter, UPDATE_OBJ)
                menu = Gtk.Menu()
                menuItem = Gtk.MenuItem.new_with_mnemonic(_("Ignore the current update for this package"))
                menuItem.connect("activate", self.add_to_ignore_list, package_update, True)
                menu.append(menuItem)
                menuItem = Gtk.MenuItem.new_with_mnemonic(_("Ignore all updates for this package"))
                menuItem.connect("activate", self.add_to_ignore_list, package_update, False)
                menu.append(menuItem)
                menu.attach_to_widget (widget, None)
                menu.show_all()
                menu.popup_at_pointer(None)
                GObject.Value.unset(package_update)

    def add_to_ignore_list(self, _widget, package_update, versioned):
        message = _("Are you sure you want to ignore %s?" % \
            f"<b>{GLib.markup_escape_text(package_update.display_name)}</b>")
        if show_confirmation_dialog(self.window, message):
            blacklist = settings.get_strv("blacklisted-packages")
            for source_package in package_update.source_packages:
                if not versioned:
                    source_package = source_package.split("=")[0]
                blacklist.append(source_package)
            settings.set_strv("blacklisted-packages", blacklist)
            self.logger.write("Ignore list change requires a refresh")
            self.refresh()
            check_export_blacklist(self.window, blacklist)

######### SYSTRAY #########

    def create_tray_icon(self, *_args):
        if self.use_appindicator:
            self.status_icon = AppIndicator.Indicator.new("mintUpdate", 'mintupdate',
                AppIndicator.IndicatorCategory.SYSTEM_SERVICES)
            self.status_icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.status_icon.set_menu(self.tray_menu)
            try:
                self.status_icon.set_title(_("Update Manager"))
            except:
                pass
            # self.status_icon.set_secondary_activate_target(None) # Middle click menu
            # self.status_icon.connect('scroll-event', self.on_statusicon_clicked)
        else:
            try:
                self.status_icon = Gtk.StatusIcon.new_from_icon_name("mintupdate")
                self.status_icon.connect('activate', self.on_statusicon_clicked)
                self.status_icon.connect('popup-menu', self.show_statusicon_menu, self.tray_menu)
                Gtk.IconTheme.get_default().connect("changed", self.tray_icon_style_update)
            except:
                settings.set_boolean("use-appindicator", True)

    def tray_icon_style_update(self, *_args):
        icon = self.status_icon.get_icon_name()
        tooltip = self.status_icon.get_tooltip_text()
        self.set_status_icon(icon, tooltip)

    def set_use_appindicator(self, _settings, key):
        old_use_appindicator = self.use_appindicator
        new_use_appindicator = self.HAVE_APP_INDICATOR and settings.get_boolean(key)
        if not self.status_icon:
            # Create new icon
            self.use_appindicator = new_use_appindicator
            self.create_tray_icon()
        elif old_use_appindicator != new_use_appindicator:
            # Get current status
            if self.use_appindicator:
                icon = self.status_icon.get_icon()
                tooltip = self.status_icon.get_icon_desc()
            else:
                icon = self.status_icon.get_icon_name()
                tooltip = self.status_icon.get_tooltip_text()
            # Re-create the icon
            self.use_appindicator = new_use_appindicator
            self.create_tray_icon()
            # Restore status
            if icon and tooltip:
                self.set_status_icon(icon, tooltip)

    def toggle_status_icon(self, *_args):
        if self.use_appindicator:
            icon = self.status_icon.get_icon()
        else:
            icon = self.status_icon.get_icon_name()
        visible = not settings.get_boolean("hide-systray") or \
            icon in ("mintupdate-error", "mintupdate-updates-available")
        if self.use_appindicator:
            if visible:
                self.status_icon.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            else:
                self.status_icon.set_status(AppIndicator.IndicatorStatus.PASSIVE)
        else:
            self.status_icon.set_visible(visible)

    def set_status_icon(self, icon, tooltip):
        if self.use_appindicator:
            self.status_icon.set_icon_full(icon, tooltip)
        else:
            self.status_icon.set_from_icon_name(icon)
            self.status_icon.set_tooltip_text(tooltip)
        self.toggle_status_icon()

    @staticmethod
    def show_statusicon_menu(_icon, _button, _activate_time, menu):
        menu.show_all()
        menu.popup_at_pointer(None)

    def tray_menu_set_sensitive(self, status, allow_quit):
        for menu_item in self.tray_menu_items:
            menu_item.set_sensitive(status)
        self.tray_menu_quit.set_sensitive(allow_quit)

    def toggle_hidden(self, hidden):
        if self.app_hidden == hidden:
            return
        self.app_hidden = hidden
        if hidden:
            self.toggle_menu_item.set_label(_("Show"))
            self.toggle_menu_item.set_image(
                Gtk.Image.new_from_icon_name("view-restore-symbolic", Gtk.IconSize.MENU))
        else:
            self.toggle_menu_item.set_label(_("Hide"))
            self.toggle_menu_item.set_image(
                Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))

    def on_statusicon_clicked(self, *_args):
        if self.app_hidden:
            self.show_window()
        else:
            self.close_window(self.window)

######### EDIT MENU CALLBACKS #########

    def open_preferences(self, _widget=None):
        if not self.preferences_window and self.tray_menu_preferences.get_sensitive():
            self.preferences_window = Preferences(self)
            self.preferences_window.show()

    @staticmethod
    def open_repositories(_widget=None):
        subprocess.Popen(["pkexec", ROOT_FUNCTIONS, "open-software-sources"] + PKEXEC_ENV)

    @staticmethod
    def open_timeshift(_widget=None):
        subprocess.Popen(["pkexec", ROOT_FUNCTIONS, "open-timeshift-gtk"] + PKEXEC_ENV)

    @staticmethod
    def open_rel_upgrade(_widget):
        subprocess.Popen(["/usr/bin/mint-release-upgrade"])

######### TOOLS MENU CALLBACKS #########

    def open_kernels(self, _widget=None):
        if not self.kernel_window_showing and not self.kernel_window and \
           self.tray_menu_kernels.get_sensitive():
            # Check if kernel manager stand-alone is running
            try:
                _pid = os.getpid()
                _uid = os.getuid()
                for proc in psutil.process_iter():
                    if proc.pid == _pid or proc.uids().real != _uid:
                        # ignore processes from other users and this process
                        continue
                    elif proc.name() == "kernel-manager":
                        self.logger.write("Ignoring request to open Kernel Manager, "
                            f"stand-alone version is running (pid: {proc.pid})")
                        return
            except:
                pass
            self.kernel_window_showing = True
            self.kernel_window = KernelWindow(self)

    def open_history(self, _widget=None):
        if not self.history_window_showing:
            HistoryWindow(self)
        self.history_window_showing.window.present_with_time(Gtk.get_current_event_time())

    def open_information(self, _widget=None):
        if not self.information_window_showing:
            LogView(self)
        self.information_window_showing.present_with_time(Gtk.get_current_event_time())

######### HELP MENU CALLBACKS #######

    def open_shortcuts(self, _widget):
        builder = Gtk.Builder.new_from_string(
            localized_ui("/usr/share/linuxmint/mintupdate/shortcuts.ui", _), -1)
        window = builder.get_object("shortcuts")
        window.connect("destroy", Gtk.Widget.destroyed, window)
        if self.window != window.get_transient_for():
            window.set_transient_for(self.window)
        window.present_with_time(Gtk.get_current_event_time())

    @staticmethod
    def open_help(_widget):
        subprocess.Popen(["yelp", "help:mintupdate/index"])

    def open_about(self, _widget):
        dlg = Gtk.AboutDialog()
        dlg.set_transient_for(self.window)
        dlg.set_title(_("About"))
        dlg.set_program_name(_("Update Manager"))
        try:
            with open('/usr/share/common-licenses/GPL', encoding="utf-8") as f:
                dlg.set_license(f.read())
        except:
            self.logger.write_error(f"Exception loading license:\n{traceback.format_exc()}")

        dlg.set_version("__DEB_VERSION__")
        dlg.set_icon_name("mintupdate")
        dlg.set_logo_icon_name("mintupdate")
        dlg.set_website("https://launchpad.net/~gm10/+archive/ubuntu/linuxmint-tools")
        def close(w, res):
            if res in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT):
                w.destroy()
        dlg.connect("response", close)
        dlg.show()
