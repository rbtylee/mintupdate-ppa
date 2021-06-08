import subprocess
import threading
import time
import traceback

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from mintcommon import apt_changelog

from common import settings
from common.constants import PRIORITY_UPDATES
from common.functions import dpkg_locked
from main.constants import DISTRO_INFO, UPDATE_OBJ, UPDATE_SORT_STR
from main.functions import size_to_string
from main.Update import Update


class RefreshThread(threading.Thread):

    def __init__(self, application, root_mode=False):
        threading.Thread.__init__(self, daemon=True)
        self.root_mode = root_mode
        self.application = application
        self.application_window = None
        self.is_self_update = False

    def __del__(self):
        self.application.refreshing = False
        self.application.cache_watcher.resume()
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_finalize)

    def run(self):
        self.application.refreshing = True
        self.application.cache_watcher.pause()

        if self.application.refresh_inhibited:
            self.application.logger.write("Refresh temporarily inhibited")
            while self.application.refresh_inhibited:
                time.sleep(5)
        if self.root_mode:
            while dpkg_locked():
                self.application.logger.write("Package management system locked by another process, retrying in 60s")
                time.sleep(60)

        try:
            if self.root_mode:
                self.application.logger.write("Starting refresh (retrieving lists of updates from remote servers)")
            else:
                self.application.logger.write("Starting refresh (local only)")
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_init)

            # Refresh the APT cache
            if self.root_mode:
                refresh_command = ["sudo", "/usr/bin/mint-refresh-cache"]
                if not self.application.app_hidden:
                    refresh_command.append("--use-synaptic")
                    refresh_command.append(
                        str(self.application.window.get_window().get_xid()))
                refresh_command.append("--mintupdate")
                if settings.get_boolean("update-mintinstall-pkgcache"):
                    refresh_command.append("--mintinstall")
                subprocess.run(refresh_command)
                settings.set_int64("refresh-last-run", int(time.time()))

            output = subprocess.run("/usr/lib/linuxmint/mintUpdate/checkAPT.py",
                                    stdout=subprocess.PIPE).stdout.decode("ascii")

            # Check presence of Mint layer
            if len(output) > 0 and not "CHECK_APT_ERROR" in output and not self.policy_check():
                return False

            # Return on error
            if "CHECK_APT_ERROR" in output:
                try:
                    error_msg = output.split("Error: ")[1].replace("E:", "\n").strip()
                    if "apt.cache.FetchFailedException" in output and " changed its " in error_msg:
                        error_msg += "\n\n%s" % _("Run 'apt update' in a terminal window to address this")
                except:
                    error_msg = ""
                self.application.logger.write_error("Error in checkAPT.py, could not refresh the list of updates")
                Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_show_error, error_msg)
                return False

            # Look at the updates one by one
            model = Gtk.TreeStore(str, str, str, str, str, int, str, str, str, str, str, object)
            #  Set pre-sort column (saved sort column will be restored afterwards)
            model.set_sort_column_id(UPDATE_SORT_STR, Gtk.SortType.ASCENDING)
            num_visible = 0
            lines = output.split("---EOL---")
            if len(lines):
                for line in lines:
                    if not "###" in line:
                        continue

                    # Create update object
                    update = Update(package=None, input_string=line, source_name=None)

                    # Check if self-update is needed
                    if update.source_name in PRIORITY_UPDATES:
                        self.is_self_update = True

                    shortdesc = update.short_description
                    if len(shortdesc) > 100:
                        try:
                            shortdesc = shortdesc[:100]
                            # Remove the last word.. in case we chomped
                            # a word containing an &#234; character..
                            # if we ended up with &.. without the code and ; sign
                            # pango would fail to set the markup
                            words = shortdesc.split()
                            shortdesc = " ".join(words[:-1]) + "…"
                        except:
                            pass

                    update_name = f"<b>{GLib.markup_escape_text(update.display_name)}</b>"
                    if settings.get_boolean("show-descriptions"):
                        update_name += f"\n{GLib.markup_escape_text(shortdesc)}"

                    origin = update.origin
                    # Pretty-print some origins
                    if origin == "linuxmint":
                        origin = "Linux Mint"
                    elif origin.startswith("LP-PPA-"):
                        origin = origin.replace("LP-PPA-", "PPA: ", 1)

                    type_sort_key = 0
                    if update.type == "kernel":
                        tooltip = _("Kernel update")
                        type_sort_key = 2
                    elif update.type == "security":
                        tooltip = _("Security update")
                        type_sort_key = 1
                    elif update.type == "unstable":
                        tooltip = _("Unstable software. Only apply this update to help developers beta-test new software.")
                        type_sort_key = 5
                    else:
                        if origin.lower() in ["ubuntu", "debian", "linux mint", "canonical"]:
                            tooltip = _("Software update")
                            type_sort_key = 3
                        else:
                            update.type = "3rd-party"
                            tooltip = "%s\n%s" % (_("3rd-party update"), origin)
                            type_sort_key = 4

                    if update.origin == "ubuntu" and update.archive.startswith("mainline-"):
                        archive = '-'.join(update.archive.split('-')[:-1])
                    else:
                        archive = update.archive

                    # UPDATE_CHECKED, UPDATE_DISPLAY_NAME, UPDATE_OLD_VERSION, UPDATE_NEW_VERSION,
                    # UPDATE_SOURCE, UPDATE_SIZE, UPDATE_SIZE_STR,
                    # UPDATE_TYPE_PIX, UPDATE_TYPE, UPDATE_TOOLTIP,
                    # UPDATE_SORT_STR, UPDATE_OBJ
                    model.append(None, row=("true", update_name, update.old_version, update.new_version,
                        f"{origin} / {archive}", update.size, size_to_string(update.size),
                        f"mintupdate-type-{update.type}-symbolic", update.type, tooltip,
                        f"{str(type_sort_key)}{update.display_name}", update))

                # Restore saved sort column
                model.set_sort_column_id(settings.get_int("sort-column-id"),
                                         settings.get_int("sort-order"))
                Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.treeview.set_model, model)
                # We need to hide the notebook here again because the line above
                # shows it for some reason
                Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.notebook_details.hide)

                # Updates found, update status page and message
                num_visible = len(model)
                if num_visible:
                    self.application.logger.write(f"Found {num_visible} software updates")
                    Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_show_updates, model, num_visible)

            # Check for infobars to display
            thread = threading.Event()
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT,
                self.application.infobars.run_status_checks, self.root_mode, thread)
            thread.wait()

            # All done, show status message
            if not len(lines) or not num_visible:
                if self.application.is_end_of_life:
                    NO_UPDATES_MSG = _("Your distribution has reached end of life and is no longer supported")
                    log_msg = "System is end of life, no updates available"
                    tray_icon = "mintupdate-error"
                    status_icon = "emblem-important-symbolic"
                else:
                    NO_UPDATES_MSG = _("Your system is up to date")
                    tray_icon = "mintupdate-up-to-date"
                    status_icon = "object-select-symbolic"
                    log_msg = "System is up to date"
                Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT,
                    self._GUI_show_no_updates, NO_UPDATES_MSG, tray_icon, status_icon)
                self.application.logger.write(log_msg)

            self.application.logger.write("Refresh finished")

        except:
            self.application.logger.write_error(
                f"Exception occurred in the refresh thread:\n{traceback.format_exc()}")
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.set_status_icon,
                "mintupdate-error", _("Could not refresh the list of updates"))

    def check_policy(self):
        """ Check the presence of the Mint layer """
        p = subprocess.run(['apt-cache', 'policy'], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", env={"LC_ALL": "C"})
        output = p.stdout
        if p.stderr:
            error_msg = p.stderr.strip()
            self.application.logger.write_error(f"APT policy error:\n{error_msg}")
        else:
            error_msg = ""
        mint_layer_found = False
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("700") and line.endswith("Packages") and "/upstream" in line:
                mint_layer_found = True
                break
        return mint_layer_found, error_msg

    def policy_check(self):
        if DISTRO_INFO["ID"] != "LinuxMint":
            return True
        mint_layer_found, error_msg = self.check_policy()
        if not mint_layer_found:
            self.application.logger.write_error("Error: The APT policy is incorrect!")
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_show_policy_error, error_msg)
        return mint_layer_found

    def _GUI_init(self):
        self.application.notebook_details.hide()
        self.application.notebook_details.set_current_page(settings.get_enum("window-pane-default-tab"))
        # Switch to status_refreshing page
        self.application.status_refreshing_spinner.start()
        self.application.show_page("status_refreshing")
        self.application_window = self.application.window.get_window()
        if self.application_window:
            self.application_window.set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.application.set_sensitive(False, set_toolbar_buttons=True)

        # Starts the blinking
        self.application.set_status_icon("mintupdate-checking", _("Checking for updates"))

    def _GUI_show_no_updates(self, msg, tray_icon, status_icon):
        self.application.builder.get_object("label_success").set_text(msg)
        self.application.builder.get_object("image_success_status").set_from_icon_name(status_icon, 96)
        self.application.show_page("status_updated")
        self.application.set_status_icon(tray_icon, msg)

    def _GUI_show_updates(self, model, num_visible):
        automatic_self_update = settings.get_boolean("automatic-self-update")
        # Self-updates:
        if self.is_self_update:
            CHANGELOG_HEIGHT = 250
            changelog_container = self.application.builder.get_object("changelog_self-update")
            textview = self.application.builder.get_object("textview_self-update")
            textview.set_right_margin(
                changelog_container.get_vscrollbar().get_preferred_width().natural_width)
            changelog_container.set_size_request(-1, CHANGELOG_HEIGHT)
            self.application.show_page("status_self-update")
            self.application.statusbar.set_opacity(0)
            message = _("Update Manager needs to be updated")
            self.application.set_status_icon("mintupdate-updates-available", message)
            # Toggle the automatic-self-update checkbutton
            # If toggled on the event triggers MintUpdate.self_update() right away!
            self.application.builder.get_object("automatic-self-update").set_active(automatic_self_update)
            if automatic_self_update:
                self.application.logger.write(f"Automatic self-update starting")
            # Show changelog of mintupdate or otherwise the first package
            # Disable some pylint false-positives with the TreeStore:
            # pylint: disable=E1133, E1136
            textview.get_buffer().set_text(_("Retrieving changelog…") + "\n" + " " * 80)
            package_version = "".join(
                [row[UPDATE_OBJ].source_packages[0] for row in model if row[UPDATE_OBJ].source_name == "mintupdate"])
            if package_version:
                package_name = "mintupdate"
            else:
                package_name = model[0][UPDATE_OBJ].source_name
                package_version = model[0][UPDATE_OBJ].source_packages[0]
            changelog = ""
            if package_name:
                # Try to load application cached changelog, on error retrieve a new one
                try:
                    changelog = self.application.self_update_changelog[package_version]
                except (AttributeError, KeyError):
                    _apt_changelog = apt_changelog.AptChangelog()
                    changelog = _apt_changelog.get_changelog(package_name)
                    self.application.self_update_changelog = { package_version: changelog }
            if not changelog:
                changelog = _("No changelog available")
            if not self.application.window.get_visible():
                # Workaround for the automatic resizing of the changelog container not working correctly
                # while the app is hidden
                self.application.window.connect("show", self.application.show_self_update_changelog,
                                                CHANGELOG_HEIGHT, changelog)
            else:
                self.application.show_self_update_changelog(None, CHANGELOG_HEIGHT, changelog)

        # Regular updates:
        else:
            # Show updates available page
            self.application.show_page("updates_available")
            # Enable Clear and Select toolbar buttons
            self.application.tool_clear.set_sensitive(True)
            self.application.tool_select_all.set_sensitive(True)
            # Set status
            num_visible = len(self.application.treeview.get_model())
            tooltip = ngettext("%d update available",
                               "%d updates available", num_visible) % num_visible
            self.application.set_status_icon("mintupdate-updates-available", tooltip)
            self.application.set_status_message_selected()
            # Show desktop notification
            self.application.show_notification(_("Update Manager"), tooltip,
                "mintupdate-updates-available", show_button=True)

    def _GUI_show_error(self, error_msg):
        self.application.set_status_icon("mintupdate-error",
            "%s%s%s" % (_("Could not refresh the list of updates"), "\n\n" * bool(error_msg), error_msg))
        self.application.show_page("status_error")
        self.application.builder.get_object("label_error_details").set_text(error_msg)
        self.application.builder.get_object("label_error_details").show()

    def _GUI_show_policy_error(self, error_msg):
        label1 = _("APT's cache or configuration are corrupt.")
        label2 = _("Do not install or update anything, it could break your operating system!")
        # TRANSLATORS: "Delete cached repository indexes" is a button in the software-sources tool, please use the same translation here
        label3 = _("Open Software Sources and try to either delete cached repository indexes via that maintenance option or select different mirrors.")
        error_label = _("APT error:")
        if error_msg:
            error_msg = f"\n\n{error_label}\n{error_msg}"
        self.application.infobars.show_infobar("software-sources",
            label1, label3, Gtk.MessageType.ERROR,
            callback=self.application.infobars.on_infobar_softwaresources_response)
        self.application.set_status_icon("mintupdate-error", f"{label1}\n{label2}")
        self.application.show_page("status_error")
        self.application.builder.get_object("label_error_details").set_markup(
            f"<b>{label1}\n{label2}\n{label3}{error_msg}</b>")
        self.application.builder.get_object("label_error_details").show()

    def _GUI_finalize(self):
        self.application.status_refreshing_spinner.stop()
        # Make sure we're never stuck on the status_refreshing page:
        if self.application.stack.get_visible_child_name() == "status_refreshing":
            self.application.show_page("updates_available")
        if self.application_window:
            self.application_window.set_cursor(None)
        if not self.is_self_update:
            self.application.set_sensitive(True)
        else:
            self.application.file_menu.set_sensitive(True)
            self.application.tray_menu_refresh.set_sensitive(True)
        self.application.tool_refresh.set_sensitive(True)
