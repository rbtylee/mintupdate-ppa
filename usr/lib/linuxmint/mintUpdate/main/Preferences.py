import json
import os
import subprocess
import threading
import traceback

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk

import dateutil.parser
from mintcommon.localization import localized_ui

from common import settings
from common.constants import ROOT_FUNCTIONS
from common.functions import check_timeshift, get_max_snapshots, read_file
from main.functions import check_export_blacklist, export_automation_user_data

# import AUTOMATIONS dict
with open("/usr/share/linuxmint/mintupdate/automation/index.json") as _f:
    AUTOMATIONS = json.load(_f)

class Preferences:

    def __init__(self, application):
        self.application = application
        self.refresh_required = False
        self.blacklist_changed = False
        self.builder = None
        self.window = None
        self.auto_upgrade_optionsfile = "/etc/mintupdate-automatic-upgrades.conf"
        self.auto_upgrade_options = []
        # TRANSLATORS: This is a tag added to package names on the ignore list
        # if a package is on hold in apt - see `man apt-mark`. In case of doubt
        # leave this untranslated.
        self.apt_hold_tag = "<b>%s</b>" % GLib.markup_escape_text(_("[apt hold]"))

    def show(self):
        self.application.set_sensitive(False)
        self.builder = Gtk.Builder.new_from_string(
            localized_ui("/usr/share/linuxmint/mintupdate/preferences.ui", _), -1)
        self.builder.add_from_string(
            localized_ui("/usr/share/linuxmint/mintupdate/preferences.kernels.ui", _))
        self.window = self.builder.get_object("main_window")
        self.window.set_transient_for(self.application.window)
        self.minimize_handler = self.window.connect("window-state-event", self.on_minimize)
        self.destroy_handler = self.window.connect("destroy", self.close)

        # Set up the stack
        self._init_stack()

        # Show the window
        if self.application.app_hidden:
            self.window.set_skip_taskbar_hint(False)
        self.window.show_all()
        self.window.present_with_time(Gtk.get_current_event_time())

        # Initialize widgets and callbacks on Options page
        self._init_page_options()

        # Initialize Refresh page
        self._init_page_refresh()

        # Initialize Blacklist page
        self._init_page_blacklist()

        # Initialize Automation page
        self._init_page_automation()

        # Initialize Expert Options page
        self._init_page_kernels()

########## INITALIZE STACK PAGES ##########

    def _init_stack(self):
        switch_container = self.builder.get_object("switch_container")
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_transition_duration(150)
        stack_switcher = Gtk.StackSwitcher()
        stack_switcher.set_stack(stack)
        switch_container.pack_start(stack_switcher, True, True, 0)
        stack_switcher.set_halign(Gtk.Align.CENTER)

        page_holder = self.builder.get_object("page_container")
        page_holder.add(stack)

        stack.add_titled(self.builder.get_object("page_options"), "page_options", _("General"))
        stack.add_titled(self.builder.get_object("page_refresh"), "page_refresh", _("Refresh"))
        stack.add_titled(self.builder.get_object("page_blacklist"), "page_blacklist", _("Ignore List"))
        stack.add_titled(self.builder.get_object("page_auto"), "page_auto", _("Automation"))
        stack.add_titled(self.builder.get_object("page_kernels"), "page_kernels", _("Expert"))

    def _init_page_options(self):
        self.initialize_widget("hide-window-after-update")
        self.initialize_widget("automatically-close-update-details")
        self.initialize_widget("hide-systray", self.application.toggle_status_icon)
        use_appindicator_widget = self.initialize_widget("use-appindicator")
        if not self.application.HAVE_APP_INDICATOR:
            use_appindicator_widget.set_sensitive(False)
            use_appindicator_widget.set_tooltip_text("%s\n\n%s" % (
                _("To use this option you must have a version of libappindicator and the corresponding "
                  "GObject introspection library installed."),
                _("Your desktop environment may further require an extension to be installed to show the indicator.")))
        self.initialize_widget("show-desktop-notifications", self.on_show_desktop_notifications)
        self.initialize_widget("automatic-self-update")
        automated_snapshots_widget = self.initialize_widget("automated-snapshots", self.on_automated_snapshots_toggled)
        self.on_automated_snapshots_toggled(automated_snapshots_widget)
        self.initialize_widget("automated-snapshots-confirmation")
        max_snapshots = self.builder.get_object("max-snapshots")
        max_snapshots.set_value(get_max_snapshots())
        max_snapshots.connect("value-changed", self.on_max_snapshots_changed)

        # Set up the window pane default tab dropdown
        dropdown = self.builder.get_object("window-pane-default-tab")
        window_pane_tabs = (_("Packages"), _("Description"), _("Changelog"))
        for i, tab in enumerate(window_pane_tabs):
            dropdown.append(str(i), tab)
        dropdown.set_active(settings.get_enum("window-pane-default-tab"))
        def on_default_tab_changed(widget):
            tab_id = int(widget.get_active_id())
            settings.set_enum("window-pane-default-tab", tab_id)
            if not self.application.notebook_details.get_visible():
                self.application.notebook_details.set_current_page(tab_id)
        dropdown.connect("changed", on_default_tab_changed)

    def _init_page_refresh(self):
        self.initialize_widget("refresh-schedule-enabled", self.on_refresh_schedule_toggled)
        self.initialize_widget("refresh-days", self.refresh_auto_refresh)
        self.initialize_widget("refresh-hours", self.refresh_auto_refresh)
        self.initialize_widget("refresh-minutes", self.refresh_auto_refresh)
        self.initialize_widget("autorefresh-days", self.refresh_auto_refresh)
        self.initialize_widget("autorefresh-hours", self.refresh_auto_refresh)
        self.initialize_widget("autorefresh-minutes", self.refresh_auto_refresh)
        self.builder.get_object("refresh_grid").set_visible(self.application.refresh_schedule_enabled)
        update_mintinstall_pkgcache = self.initialize_widget("update-mintinstall-pkgcache")
        if not os.path.isfile("/usr/bin/mintinstall-update-pkgcache"):
            update_mintinstall_pkgcache.set_active(False)
            update_mintinstall_pkgcache.set_tooltip_text(_("Requires Software Manager (mintinstall) to be installed"))
            update_mintinstall_pkgcache.set_sensitive(False)

    def _init_page_blacklist(self):
        treeview_blacklist = self.builder.get_object("treeview_blacklist")
        column = Gtk.TreeViewColumn(_("Ignored Updates"), Gtk.CellRendererText(), markup=1)
        column.set_sort_column_id(0)
        column.set_resizable(True)
        treeview_blacklist.append_column(column)
        treeview_blacklist.show()
        model = Gtk.TreeStore(str, str)
        model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        treeview_blacklist.set_model(model)
        blacklist = settings.get_strv("blacklisted-packages")
        for ignored_pkg in blacklist:
            tree_iter = model.insert_before(None, None)
            model.set_value(tree_iter, 0, ignored_pkg)
            model.set_value(tree_iter, 1, GLib.markup_escape_text(ignored_pkg))
        # Import packages held by APT
        threading.Thread(target=self.do_get_held_packages, args=(model,), daemon=False).start()
        self.builder.get_object("button_add").connect("clicked", self.add_blacklisted_package, treeview_blacklist)
        self.builder.get_object("button_remove").connect("clicked", self.remove_blacklisted_package, treeview_blacklist)

    def _init_page_automation(self):
        for automation_id in ("upgrade", "autoremove"):
            self.initialize_automation(automation_id)
            self.automation_add_time_labels(automation_id)
        if os.path.isfile(self.auto_upgrade_optionsfile):
            for line in read_file(self.auto_upgrade_optionsfile):
                line = line.strip()
                if line and not line.startswith("#"):
                    self.auto_upgrade_options.append(line)
        self.builder.get_object("export_blacklist_button").connect("clicked", self.export_blacklist)
        self.initialize_auto_upgrade_option_widget("auto_upgrade_option_only-kernel", "--only-kernel",
            "auto_upgrade_option_only-security")
        self.initialize_auto_upgrade_option_widget("auto_upgrade_option_only-security", "--only-security",
            "auto_upgrade_option_only-kernel")
        self.initialize_auto_upgrade_option_widget("auto_upgrade_option_mainline", "--mainline")
        self.initialize_auto_upgrade_option_widget("auto_upgrade_option_keep-configuration", "--keep-configuration")
        self.initialize_auto_upgrade_option_widget("auto_upgrade_option_install-recommends", "--install-recommends")
        self.initialize_auto_upgrade_option_widget("auto_upgrade_option_snapshots", "--create-snapshot")
        self.initialize_widget("enable-notifier", self.on_enable_notifier)

    def _init_page_kernels(self):
        # (this is duplicated in kernel-manager.py and should be kept in sync)
        use_mainline_kernels_widget = self.initialize_widget("use-mainline-kernels", self.on_use_mainline_toggle)
        self.initialize_widget("mainline-include-rc")
        self.initialize_widget("mainline-include-longterm")
        self.initialize_widget("mainline-upgrade-eol-series", self.set_refresh_required)
        self.initialize_widget("allow-kernel-type-selection")
        self.builder.get_object("mainline_options").set_visible(use_mainline_kernels_widget.get_active())

########## COMMON FUNCTIONS ##########

    def initialize_widget(self, name, additional_callback=None):
        """
        params:

        `name` - the id of the widget to initialize as well as of the gsettings key to bind it to.

        `additional_callback` - optional callback function
        """
        widget = self.builder.get_object(name)
        # Set value
        if isinstance(widget, Gtk.CheckButton):
            event = "toggled"
            widget.set_active(settings.get_boolean(name))
        elif isinstance(widget, Gtk.SpinButton):
            event = "value-changed"
            widget.set_value(settings.get_int(name))
        # Connect callbacks
        widget.connect(event, self.on_setting_toggled, name)
        if additional_callback:
            widget.connect(event, additional_callback)
        return widget

    def initialize_automation(self, automation_id):
        """
        params:

        `automation_id` - the id as configured in AUTOMATIONS
        """
        widget = self.builder.get_object(f"auto_{automation_id}_checkbox")
        widget.set_active(os.path.isfile(AUTOMATIONS[automation_id][0]))
        widget.connect("toggled", self.set_automation, automation_id)

    def initialize_auto_upgrade_option_widget(self, widget_id, option, disables=None):
        """
        params:

        `widget_id` - the id of the widget to initialize

        `option` - the option to pass on to mintupdate-cli

        `disables` - the widget_id of a widget to disable when this widget gets enabled
        """
        widget = self.builder.get_object(widget_id)
        # Set value
        widget.set_active(option in self.auto_upgrade_options)
        # Connect callbacks
        widget.connect("toggled", self.on_auto_upgrade_option_toggled, option, disables)

    def export_user_data(self, automation_id, data:list):
        """ Writes `data` to disk, returns a bool with the result """
        self.window.set_sensitive(False)
        thread = threading.Thread(target=export_automation_user_data, args=(automation_id, data), daemon=False)
        thread.start()
        while thread.is_alive():
            Gtk.main_iteration()
        self.window.set_sensitive(True)
        return thread.result

    def close(self, _widget):
        self.window.disconnect(self.destroy_handler)
        self.window.disconnect(self.minimize_handler)
        self.window.destroy()
        self.application.set_sensitive(True)
        if self.refresh_required:
            self.application.logger.write("Preferences changes require a refresh")
            self.application.refresh()
        self.application.preferences_window = None
        if self.blacklist_changed:
            check_export_blacklist(self.application.window)

########## COMMON CALLBACKS ##########

    @staticmethod
    def on_setting_toggled(widget, setting):
        if isinstance(widget, Gtk.CheckButton):
            settings.set_boolean(setting, widget.get_active())
        elif isinstance(widget, Gtk.SpinButton):
            settings.set_int(setting, int(widget.get_value()))

    def set_refresh_required(self, _widget):
        self.refresh_required = True

    def on_minimize(self, _widget, event):
        """
        Minimizes the main window when the preferences window gets minimized.
        This is necessary for UX because we do not show a taskbar hint anymore.
        """
        if not self.application.app_hidden and event.new_window_state & Gdk.WindowState.ICONIFIED:
            self.application.window.iconify()

########## AUTOMATION CALLBACKS & FUNCTIONS ##########

    def on_auto_upgrade_option_toggled(self, widget, option, disables):
        """ Handle the automatic upgrade options getting toggled """
        if widget.get_active():
            if not option in self.auto_upgrade_options:
                self.auto_upgrade_options.append(option)
            if disables:
                disables_widget = self.builder.get_object(disables)
                if disables_widget.get_active():
                    disables_widget.set_active(False)
                    # Return here and let the other widget's callback handle
                    # the conf file update
                    return
        else:
            if option in self.auto_upgrade_options:
                self.auto_upgrade_options.remove(option)
        if not self.export_user_data("upgrade-options", self.auto_upgrade_options):
            # Option export failed, undo the GUI changes
            widget.handler_block_by_func(self.on_auto_upgrade_option_toggled)
            active = widget.get_active()
            widget.set_active(not active)
            widget.handler_unblock_by_func(self.on_auto_upgrade_option_toggled)
            if disables:
                disables_widget = self.builder.get_object(disables)
                disables_widget.handler_block_by_func(self.on_auto_upgrade_option_toggled)
                disables_widget.set_active(active)
                disables_widget.handler_unblock_by_func(self.on_auto_upgrade_option_toggled)

    def on_enable_notifier(self, widget):
        """ Toggle the mintupdate-automation-notifier user service """
        try:
            if widget.get_active():
                subprocess.run(["systemctl", "--user", "enable", "mintupdate-automation-notifier.timer"])
            else:
                subprocess.run(["systemctl", "--user", "disable", "mintupdate-automation-notifier.timer"])
        except:
            self.application.logger.write(f"Exception in on_enable_notifier:\n{traceback.format_exc()}")

    def set_automation(self, widget, automation_id):
        self.window.set_sensitive(False)
        thread = threading.Thread(target=self.do_set_automation, args=(widget, automation_id), daemon=False)
        thread.start()
        while thread.is_alive():
            Gtk.main_iteration()
        self.window.set_sensitive(True)
        active = widget.get_active()
        if automation_id == "upgrade":
            if not active:
                self.builder.get_object("enable-notifier").set_active(False)
        self.automation_remove_time_labels(automation_id)
        if active:
            self.automation_add_time_labels(automation_id)

    def do_set_automation(self, widget, automation_id):
        active = widget.get_active()
        exists = os.path.isfile(AUTOMATIONS[automation_id][0])
        action = None
        if active and not exists:
            action = "enable"
        elif not active and exists:
            action = "disable"
        if action:
            try:
                subprocess.run(["pkexec", ROOT_FUNCTIONS, "automation", automation_id, action], check=True)
            except subprocess.CalledProcessError:
                self.application.logger.write_error(f"Failed to {action} automation task `{automation_id}`")
            except:
                self.application.logger.write_error(
                    f"Exception trying to {action} automation task `{automation_id}`:\n{traceback.format_exc()}")
            active = os.path.isfile(AUTOMATIONS[automation_id][0])
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, widget.set_active, active)

    def automation_add_time_labels(self, automation_id):
        """ Get timer status and add labels with time for Last Run: and Next Run: """
        if not self.builder.get_object(f"auto_{automation_id}_checkbox").get_active():
            return
        try:
            cmd = ["systemctl", "show", f"mintupdate-automation-{automation_id}.timer",
                    "--no-page", "-p", "LastTriggerUSec,NextElapseUSecRealtime"]
            status = subprocess.run(cmd, stdout=subprocess.PIPE, encoding="utf-8").stdout
            if status:
                box = self.builder.get_object(f"auto_{automation_id}_time_labels")
                for line in sorted(status.split("\n")):
                    line = line.split("=")
                    if not len(line) == 2 or not line[1]:
                        continue
                    try:
                        dt = dateutil.parser.parse(line[1])
                        dt_string = dt.strftime("%x %X")
                    except:
                        dt_string = line[1]
                    if line[0] == "NextElapseUSecRealtime":
                        label = Gtk.Label.new(_("Next Run: %s") % dt_string)
                        label.set_name(f"auto_{automation_id}_next_run")
                    elif line[0] == "LastTriggerUSec":
                        label = Gtk.Label.new(_("Last Run: %s") % dt_string)
                    box.add(label)
                box.show_all()
        except:
            pass

    def automation_remove_time_labels(self, automation_id):
        """ Remove time labels that were added by `self.automation_add_time_labels()` """
        box = self.builder.get_object(f"auto_{automation_id}_time_labels")
        for child in box:
            box.remove(child)

########## OPTIONS CALLBACKS ##########

    def on_show_desktop_notifications(self, widget):
        self.application.show_notifications = widget.get_active()

    def on_automated_snapshots_toggled(self, widget):
        is_active = widget.get_active()
        if is_active and not check_timeshift():
            dialog = Gtk.MessageDialog(transient_for=self.application.window,
                modal=True, destroy_with_parent=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=_("Timeshift must be installed and set up before automated system snapshots can be enabled."))
            dialog.run()
            dialog.destroy()
            widget.set_active(False)
            return
        self.builder.get_object("automated-snapshots-confirmation").set_visible(is_active)
        self.builder.get_object("automated-snapshots-options").set_visible(is_active)

    def on_max_snapshots_changed(self, widget):
        self.export_user_data("snapshot-options", [f"MAX_SNAPSHOTS={int(widget.get_value())}"])

########## REFRESH CALLBACKS & FUNCTIONS ##########

    def on_refresh_schedule_toggled(self, widget):
        self.application.refresh_schedule_enabled = widget.get_active()
        self.builder.get_object("refresh_grid").set_visible(self.application.refresh_schedule_enabled)
        self.refresh_auto_refresh()

    def refresh_auto_refresh(self, _widget=None):
        if self.application.refresh_schedule_enabled:
            self.application.restart_auto_refresh()

########## BLACKLIST CALLBACKS & FUNCTIONS ##########

    def do_get_held_packages(self, model):
        try:
            output = subprocess.run(["apt-mark", "showhold"], stdout=subprocess.PIPE, encoding="utf-8").stdout
            packages = output.split("\n")
            for package in packages:
                package = package.strip()
                if not package:
                    continue
                tree_iter = model.insert_before(None, None)
                model.set_value(tree_iter, 0, package)
                model.set_value(tree_iter, 1, f"{GLib.markup_escape_text(package)} {self.apt_hold_tag}")
        except:
            self.application.logger.write(f"Exception trying to list held packages:\n{traceback.format_exc()}")

    def do_apt_unhold(self, package):
        try:
            subprocess.run(["pkexec", ROOT_FUNCTIONS, "apt-unhold", package], check=True)
            self.success = True
        except subprocess.CalledProcessError:
            pass
        except:
            self.application.logger.write(
                f"Exception trying to unhold `{package}`:\n{traceback.format_exc()}")

    def export_blacklist(self, _widget):
        blacklist = settings.get_strv("blacklisted-packages")
        self.export_user_data("blacklist", blacklist)
        self.blacklist_changed = False


    def add_blacklisted_package(self, _widget, treeview_blacklist):
        dialog = Gtk.MessageDialog(transient_for=self.window,
                                   modal=True,
                                   destroy_with_parent=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK)
        dialog.set_title(_("Ignore an Update"))
        dialog.set_markup(_("Please specify the source package name of the update to ignore "
                            "(wildcards are supported) and optionally the version:"))
        dialog.set_icon_name("mintupdate")
        grid = Gtk.Grid()
        grid.set_column_spacing(5)
        grid.set_halign(Gtk.Align.CENTER)
        name_entry = Gtk.Entry()
        name_entry.connect("activate", lambda _: dialog.response(Gtk.ResponseType.OK))
        version_entry = Gtk.Entry()
        version_entry.connect("activate", lambda _: dialog.response(Gtk.ResponseType.OK))
        grid.attach(Gtk.Label.new(_("Name:")), 0, 0, 1, 1)
        grid.attach(name_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label.new(_("Version:")), 0, 1, 1, 1)
        grid.attach(version_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label.new(_("(optional)")), 2, 1, 1, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            name = name_entry.get_text().strip()
            version = version_entry.get_text().strip()
            if name:
                if version:
                    pkg = f"{name}={version}"
                else:
                    pkg = name
                # Update GUI
                model = treeview_blacklist.get_model()
                tree_iter = model.insert_before(None, None)
                model.set_value(tree_iter, 0, pkg)
                model.set_value(tree_iter, 1, GLib.markup_escape_text(pkg))
                # Update settings
                blacklist = settings.get_strv("blacklisted-packages")
                blacklist.append(pkg)
                settings.set_strv("blacklisted-packages", blacklist)
                self.refresh_required = True
                self.blacklist_changed = True
        dialog.destroy()

    def remove_blacklisted_package(self, _widget, treeview_blacklist):
        selection = treeview_blacklist.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter:
            package = model.get_value(tree_iter, 0)
            markup = model.get_value(tree_iter, 1)
            if self.apt_hold_tag in markup:
                self.window.set_sensitive(False)
                self.success = False
                # Remove apt hold
                thread = threading.Thread(target=self.do_apt_unhold, args=(package,), daemon=False)
                thread.start()
                while thread.is_alive():
                    Gtk.main_iteration()
                self.window.set_sensitive(True)
                if not self.success:
                    return
            else:
                # Update blacklist
                blacklist = settings.get_strv("blacklisted-packages")
                blacklist.remove(model.get_value(tree_iter, 0))
                settings.set_strv("blacklisted-packages", blacklist)
            # Update GUI
            model.remove(tree_iter)
        self.refresh_required = True
        self.blacklist_changed = True

########## KERNELS CALLBACKS ##########

    def on_use_mainline_toggle(self, widget):
        self.refresh_required = True
        self.builder.get_object("mainline_options").set_visible(widget.get_active())
