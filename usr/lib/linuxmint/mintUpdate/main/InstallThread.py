import apt_pkg
import os
import subprocess
import tempfile
import threading
import traceback

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

from common import settings
from common.constants import PKEXEC_ENV, PRIORITY_UPDATES, ROOT_FUNCTIONS
from common.dialogs import show_confirmation_dialog
from common.functions import check_timeshift, read_file
from common.MainlineKernelInstaller import MainlineKernelInstaller
from main.constants import UPDATE_CHECKED, UPDATE_OBJ


class InstallThread(threading.Thread):

    def __init__(self, application):
        threading.Thread.__init__(self, daemon=False)
        self.application = application
        self.reboot_required = self.application.reboot_required
        self.self_update = False
        self.window = self.application.window.get_window()

    def __del__(self):
        self.application.cache_watcher.resume(update_cachetime=False)
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_finalize)
        self.application.refresh_inhibited = False

    def run(self):
        self.application.refresh_inhibited = True
        self.application.cache_watcher.pause()
        try:
            self.application.logger.write("Install requested by user")
            packages = []
            mainline_updates = {}
            thread = threading.Event()
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_init, thread)
            thread.wait()
            model = thread.model
            del thread

            for row in model:
                if row[UPDATE_CHECKED] == "true":
                    package_update = row[UPDATE_OBJ]
                    if package_update.origin == "ubuntu" and package_update.archive.startswith("mainline-"):
                        mainline_branch_id = int(package_update.archive.split("-")[-1])
                        if not mainline_branch_id in mainline_updates:
                            mainline_updates[mainline_branch_id] = []
                        mainline_updates[mainline_branch_id].append(package_update)
                    else:
                        packages.extend(package_update.package_names)
                        if package_update.type == "kernel" and \
                        [True for pkg in package_update.package_names if "-image-" in pkg]:
                            self.reboot_required = True
                    for package in package_update.package_names:
                        self.application.logger.write("Will install " + str(package))

            if [True for pkg in PRIORITY_UPDATES if pkg in packages]:
                self.self_update = True

            if packages or mainline_updates:
                do_snapshot = not self.self_update and settings.get_boolean("automated-snapshots") and \
                              check_timeshift() and self.confirm_automated_snapshot()

                if settings.get_boolean("hide-window-after-update"):
                    Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.hide_main_window)

                if do_snapshot:
                    # Automated system snapshot
                    self.application.set_status("mintupdate-installing", _("Creating system snapshot"))
                    self.application.logger.write("Creating system snapshot")

                    # Assemble snapshot comment
                    package_names = packages.copy()
                    if mainline_updates:
                        for mainline_branch_id in mainline_updates:
                            for update in mainline_updates[mainline_branch_id]:
                                package_names.extend(update.package_names)
                    comment = _("Before updating: %s") % f'{", ".join(package_names)} #mintupdate'

                    # Create system snapshot and pipe timeshift output into statusbar
                    cmd = ["pkexec", ROOT_FUNCTIONS, "timeshift", comment]
                    try:
                        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, encoding="utf-8", bufsize=1)
                        failed = False
                        snapshot_done = False
                        error = None
                        stub = _("Creating system snapshot:")
                        for line in p.stdout:
                            line = line.strip("- \n")
                            if not line:
                                continue
                            # Try to get error message from timeshift for log
                            if not error and not snapshot_done:
                                if line.startswith("E: "):
                                    error = line.split("E: ", 1)[1]
                                elif "failed" in line.lower():
                                    if not error:
                                        error = line
                            # Detect if there actually was a fatal error.
                            # This is done in root_functions by comparing snapshot count.
                            if not failed and "#mintupdate-snapshot-failed" in line:
                                failed = True
                                if not error:
                                    error = _("Timeshift returned an error")
                                continue
                            # Detect when pruning begins and change status accordingly
                            elif not snapshot_done and "#mintupdate-pruning-snapshots" in line:
                                snapshot_done = True
                                stub = _("Removing old system snapshot:")
                                self.application.logger.write("Pruning existing snapshots")
                                continue
                            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT,
                                self.application.set_status_message, f"{stub} {line}")
                        p.wait()
                        if p.returncode or failed:
                            raise subprocess.CalledProcessError(p.returncode, cmd, error)
                        self.application.logger.write("System snapshot completed successfully")
                    except subprocess.CalledProcessError as e:
                        self.application.set_status("mintupdate-error", _("System snapshot failed: %s") % e.stdout)
                        self.application.logger.write_error(f"System snapshot failed: {e.stdout}")
                        self.application.logger.write_error("Install aborted")
                        return False

                proceed = True
                try:
                    pkgs = " ".join(str(pkg) for pkg in packages)
                    warnings = subprocess.run(["/usr/lib/linuxmint/mintUpdate/checkWarnings.py", pkgs],
                        stdout=subprocess.PIPE, encoding="utf-8").stdout
                    warnings = warnings.split("###")
                    if len(warnings) == 2:
                        installations = warnings[0].split()
                        removals = warnings[1].split()
                        if not self.self_update and (len(installations) > 0 or len(removals) > 0):
                            thread = threading.Event()
                            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT,
                                self._show_changes_dialog, thread, installations, removals)
                            thread.wait()
                            proceed = thread.proceed
                        else:
                            proceed = True
                except:
                    self.application.logger.write_error(f"Exception running checkWarnings.py:\n{traceback.format_exc()}")

                if not proceed:
                    self.application.set_status("mintupdate-error", _("Installation canceled"))
                    return
                else:
                    self.application.set_status("mintupdate-installing", _("Installing updates"))

                    update_successful = False
                    auto_close = settings.get_boolean("automatically-close-update-details")

                    # Mainline kernel updates (this should only ever be a single one):
                    if mainline_updates:
                        for mainline_branch_id in mainline_updates:
                            mainline = MainlineKernelInstaller(branch_id=mainline_branch_id,
                                                               transient_for=self.application.window)
                            # Download
                            for mainline_update in mainline_updates[mainline_branch_id]:
                                # versioned builds
                                if mainline_branch_id == 0:
                                    self.application.logger.write(
                                        f"Downloading mainline kernel v{mainline.base_data.format_version(mainline_update.new_version)}")
                                # daily builds
                                else:
                                    self.application.logger.write(
                                        f"Downloading mainline kernel {mainline.base_data.name} build from {mainline_update.new_version}")
                                try:
                                    downloaded_files = mainline.download_files(mainline_update.new_version, mainline_update.package_names)
                                except mainline.DownloadError as e:
                                    self.application.logger.write(e)
                                    downloaded_files = None
                                    if mainline.window:
                                        mainline.window.destroy()
                        # Install
                        if downloaded_files:
                            # self.debfiles.extend(downloaded_files)
                            # The following part is a workaround until we can get
                            # aptdaemon to support this:
                            self.application.logger.write(f"Installing mainline kernel packages")
                            retval = mainline.install(downloaded_files, is_upgrade=True, auto_close=auto_close)
                            for filename in downloaded_files:
                                os.remove(filename)
                            if not retval:
                                self.application.reboot_required = True
                                update_successful = True

                    if packages:
                        f = tempfile.NamedTemporaryFile()

                        for pkg in packages:
                            pkg_line = "%s\tinstall\n" % pkg
                            f.write(pkg_line.encode("utf-8"))
                        f.flush()

                        if self.self_update:
                            installation_mode = "self-update"
                            if self.window:
                                xid = str(self.window.get_xid())
                            else:
                                xid = ""
                            # Workaround because pkexec is bugged and ignores arguments
                            cmd = ["pkexec", "/usr/lib/linuxmint/mintUpdate/root_functions.self-update",
                                   installation_mode, xid, f.name]
                        else:
                            installation_mode = "synaptic"
                            cmd = ["pkexec", ROOT_FUNCTIONS, installation_mode,
                                   str(self.window.get_xid()), f.name,
                                   "reboot-required" * self.reboot_required,
                                   "closeZvt" * auto_close]
                        cmd.extend(PKEXEC_ENV)
                        self.application.logger.write("Launching Synaptic")
                        try:
                            result = subprocess.run(cmd, stdout=self.application.logger.log,
                                            stderr=self.application.logger.log, check=True)
                            returncode = result.returncode
                        except subprocess.CalledProcessError as e:
                            returncode = e.returncode
                        self.application.logger.write(f"Return code: {returncode}")
                        f.close()

                        latest_apt_update = ""
                        update_successful = False
                        apt_pkg.init_config()
                        history_log = apt_pkg.config.find_file("Dir::Log::History")
                        if not os.path.isfile(history_log):
                            # Fail-safe
                            update_successful = True
                        else:
                            for line in reversed(read_file(history_log)):
                                if "Start-Date" in line:
                                    break
                                else:
                                    latest_apt_update += line
                            if f.name in latest_apt_update and "End-Date" in latest_apt_update and \
                                not "Error: " in latest_apt_update:
                                update_successful = True
                                self.application.logger.write("Install finished")
                            else:
                                self.application.logger.write("Install failed")

                    if update_successful:
                        # override CacheWatcher since there's a forced refresh later already
                        self.application.cache_watcher.update_cachetime()

                        if self.reboot_required:
                            self.application.reboot_required = True

                        if self.self_update:
                            # Restart
                            self.application.logger.write("Update Manager was updated, restarting it…")
                            self.application.logger.close()
                            subprocess.Popen(["/usr/lib/linuxmint/mintUpdate/mintUpdate.py", "show", "restart"])
                            # Disable the self-update flag because otherwise we trigger code in __del__ for failed self-updates
                            self.self_update = False
                            return

                        # Refresh
                        self.application.refresh()
                    else:
                        self.application.set_status("mintupdate-error", _("Could not install the updates"))
        except:
            self.application.logger.write_error(f"Exception occurred in the install thread:\n{traceback.format_exc()}")
            self.application.set_status("mintupdate-error", _("Could not install the updates"))
            self.application.logger.write_error("Could not install the security updates")

    def confirm_automated_snapshot(self):
        if not settings.get_boolean("automated-snapshots-confirmation"):
            return True
        thread = threading.Event()
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._show_automated_snapshot_confirmation_dialog, thread)
        thread.wait()
        return thread.result

    def _show_automated_snapshot_confirmation_dialog(self, thread):
        thread.result = show_confirmation_dialog(
            transient_for=self.application.window,
            title=_("Automated System Snapshot"),
            text=_("Do you want a system snapshot to be created before installing the updates?"))
        thread.set()

    def _show_changes_dialog(self, thread, installations, removals):
        try:
            dialog = Gtk.MessageDialog(transient_for=self.application.window,
                                    modal=True,
                                    destroy_with_parent=True,
                                    message_type=Gtk.MessageType.WARNING,
                                    buttons=Gtk.ButtonsType.OK_CANCEL,
                                    title="")
            dialog.set_markup("<b>" + _("This upgrade will trigger additional changes") + "</b>")
            #dialog.format_secondary_markup("<i>" + _("All available upgrades for this package will be ignored.") + "</i>")
            dialog.set_icon_name("mintupdate")
            dialog.set_default_size(320, 400)
            dialog.set_resizable(True)

            if len(removals) > 0:
                # Removals
                label = Gtk.Label()
                label.set_text(_("The following packages will be removed:"))
                label.set_alignment(0, 0.5)
                label.set_padding(20, 0)
                scrolledWindow = Gtk.ScrolledWindow()
                scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                treeview = Gtk.TreeView()
                column = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                column.set_sort_column_id(0)
                column.set_resizable(True)
                treeview.append_column(column)
                treeview.set_headers_clickable(False)
                treeview.set_reorderable(False)
                treeview.set_headers_visible(False)
                model = Gtk.TreeStore(str)
                removals.sort()
                for pkg in removals:
                    tree_iter = model.insert_before(None, None)
                    model.set_value(tree_iter, 0, pkg)
                treeview.set_model(model)
                treeview.show()
                scrolledWindow.add(treeview)
                dialog.get_content_area().pack_start(label, False, False, 0)
                dialog.get_content_area().pack_start(scrolledWindow, True, True, 0)
                dialog.get_content_area().set_border_width(6)

            if len(installations) > 0:
                # Installations
                label = Gtk.Label()
                label.set_text(_("The following packages will be installed:"))
                label.set_alignment(0, 0.5)
                label.set_padding(20, 0)
                scrolledWindow = Gtk.ScrolledWindow()
                scrolledWindow.set_shadow_type(Gtk.ShadowType.IN)
                scrolledWindow.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
                treeview = Gtk.TreeView()
                column = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
                column.set_sort_column_id(0)
                column.set_resizable(True)
                treeview.append_column(column)
                treeview.set_headers_clickable(False)
                treeview.set_reorderable(False)
                treeview.set_headers_visible(False)
                model = Gtk.TreeStore(str)
                installations.sort()
                for pkg in installations:
                    tree_iter = model.insert_before(None, None)
                    model.set_value(tree_iter, 0, pkg)
                treeview.set_model(model)
                treeview.show()
                scrolledWindow.add(treeview)
                dialog.get_content_area().pack_start(label, False, False, 0)
                dialog.get_content_area().pack_start(scrolledWindow, True, True, 0)

            dialog.show_all()
            thread.proceed = dialog.run() == Gtk.ResponseType.OK
            dialog.destroy()
        except:
            thread.proceed = False
            self.application.logger.write_error(f"Exception showing changes dialog:\n{traceback.format_exc()}")
        thread.set()

    def _GUI_init(self, thread):
        if self.window:
            self.window.set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        self.application.set_sensitive(False, allow_quit=False)
        thread.model = self.application.treeview.get_model()
        thread.set()

    def _GUI_finalize(self):
        if self.window:
            self.window.set_cursor(None)
        if self.self_update:
            # Failed self update, re-enable widgets:
            self.application.file_menu.set_sensitive(True)
            self.application.builder.get_object("confirm-self-update").set_sensitive(True)
            self.application.builder.get_object("automatic-self-update").set_sensitive(True)
            self.application.tray_menu_quit.set_sensitive(True)
        else:
            self.application.set_sensitive(True)

