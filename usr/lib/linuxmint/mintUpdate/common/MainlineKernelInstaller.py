import os
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Vte', '2.91')
from gi.repository import Gtk, Gdk, Vte, GLib

import requests
from mintcommon.localization import localized_ui

from common.constants import ROOT_FUNCTIONS
from common.MainlineKernels import MainlineKernels


class MainlineKernelInstaller(MainlineKernels):
    """ Simple GUI to download and install mainline kernels """

    def __init__(self, branch_id=0, cached=True, flavor="", transient_for=None):
        super().__init__(branch_id=branch_id, cached=True, flavor="")
        self.window = None
        self.transient_for = transient_for
        self.deletable = True
        self.download_canceled = False

    def vte_set_status(self, status):
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.status_label.set_text, status)

    def close(self):
        if self.window:
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.window.destroy)

    def on_destroy(self, *_args):
        if self.window and not self.window.get_deletable():
            return True
        else:
            self.window = None
            return False

    def vte_spawn(self, title=None, deletable=False, destroy_cb=None):
        if not self.window:
            thread = threading.Event()
            Gdk.threads_add_idle(GLib.PRIORITY_HIGH, self._vte_spawn, thread, title, deletable, destroy_cb)
            thread.wait()

    def _vte_spawn(self, thread, title, deletable, destroy_cb):
        builder = Gtk.Builder.new_from_string(localized_ui("/usr/share/linuxmint/mintupdate/vte.ui", _), -1)
        self.window = builder.get_object("window")
        if title:
            self.window.set_title(title)
        self.window.set_deletable(deletable)
        self.window.set_transient_for(self.transient_for)
        # self.window.set_modal(True)
        self.window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        # Don't skip the taskbar hint or we'd need to tie this into the status icon toggle
        # self.window.set_skip_taskbar_hint(True)
        self.window.connect("destroy", self.on_destroy)
        if destroy_cb:
            self.window.connect("destroy", destroy_cb)
        self.window.connect("delete-event", self.on_destroy)
        self.status_label = builder.get_object("status_label")
        self.progressbar = builder.get_object("progressbar")
        terminal = Vte.Terminal()
        self.pty = Vte.Pty.new_sync(Vte.PtyFlags.DEFAULT)
        terminal.set_pty(self.pty)
        terminal.set_input_enabled(False)
        terminal.set_scrollback_lines(-1)
        self.pty.spawn_async(
            None, # cwd
            ["/bin/sh"], # argv
            ["PS1="], # envv
            GLib.SpawnFlags.DO_NOT_REAP_CHILD, # spawn_flags
            None, # child_setup
            None, # child_setup_data
            -1, # timeout
            None, # cancellable
            self.vte_ready, # callback, cannot be Null
            terminal # user_data
            )
        terminal.show()
        self.terminal_container = builder.get_object("terminal_container")
        self.terminal_container.add(terminal)
        thread.set()

    def vte_ready(self, pty, task, terminal):
        status, child_pid = pty.spawn_finish(task)
        if not status:
            self.close()
        else:
            terminal.watch_child(child_pid)

    def cancel_download(self, _widget):
        self.download_canceled = True

    def download_files(self, version, filelist):
        """
        Downloads `filelist` of packages for mainline kernel `version` into
        `self.tmpfolder`
        """
        self.download_canceled = False
        self.vte_spawn(title=_("Mainline Kernel Download"), deletable=True, destroy_cb=self.cancel_download)

        if not os.path.exists(self.tmpfolder):
            os.umask(0)
            os.makedirs(self.tmpfolder)
        base_url = self.base_data.versioned_url(version)
        session = requests.Session()
        downloaded_files = []
        # get the files
        for filename in filelist:
            self.vte_set_status(_(f"Downloading {filename}…"))
            download_failed = False
            url = base_url + filename
            try:
                r = session.get(url, stream=True, timeout=5)
            except:
                download_failed = True
            if not download_failed and r.ok:
                try:
                    length = r.headers.get("Content-Length")
                    if length:
                        r.length = int(length)
                        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._show_progressbar)
                    else:
                        r.length = 0
                    with open(self.tmpfolder + filename, "wb") as outfile:
                        recv_length = 0
                        for data in r.iter_content(chunk_size=16384):
                            if self.download_canceled:
                                raise self.DownloadError(_("Download canceled"))
                            recv_length += len(data)
                            outfile.write(data)
                            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._update_progressbar, recv_length / r.length)
                    downloaded_files.append(self.tmpfolder + filename)
                except:
                    download_failed = True
            else:
                download_failed = True
            if download_failed:
                for downloaded_file in downloaded_files:
                    if os.path.isfile(downloaded_file):
                        os.remove(downloaded_file)
                try:
                    session.close()
                except:
                    pass
                raise self.DownloadError(_(f"Failed to download {url}"))
        # verify the checksums
        verified = False
        try:
            self.vte_set_status(_(f"Verifying downloaded files…"))
            r = session.get(f"{self.base_data.versioned_url(version)}CHECKSUMS", timeout=5)
            # TODO: Checksum authentication via CHECKSUMS.gpg necessary?
            # If we do this, we should supply the key so we do not have to rely on the
            # keyserver - fingerprint 60AA7B6F30434AE68E569963E50C6A0917C622B0
            if r.ok:
                r.encoding = None
                checksums = r.text
                verified = self.verify_checksums(checksums, downloaded_files)
        except:
            pass
        if not verified:
            raise self.DownloadError(_(f"Checksum verification of downloaded files failed"))
        session.close()
        if self.window:
            Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._hide_window)
        return downloaded_files

    def _hide_window(self):
        self.progressbar.hide()
        self.window.hide()

    def _show_progressbar(self):
        self.progressbar.set_fraction(0)
        self.progressbar.show()

    def _update_progressbar(self, value):
        if self.progressbar.get_visible():
            self.progressbar.set_fraction(value)

    def install(self, debfiles, is_upgrade=False, auto_close=True):
        if not debfiles:
            return -1
        self.vte_spawn()
        def _window_init():
            self.window.set_default_size(700, 300)
            self.window.set_title(_("Mainline Kernel Installation"))
            self.window.set_deletable(False)
            self.terminal_container.show()
            self.window.show()
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, _window_init)
        self.vte_set_status(_("Installing…"))
        cmd = ["pkexec", ROOT_FUNCTIONS,  "mainline"]
        if is_upgrade:
            cmd.append("upgrade")
        else:
            cmd.append("install")
        cmd.extend(debfiles)
        child_pid = GLib.spawn_async(cmd, flags=(GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD),
                                     child_setup=self.pty.child_setup)[0]
        returncode = os.waitpid(child_pid, 0)[1]
        GLib.spawn_close_pid(child_pid)
        def _window_set_deletable():
            self.window.set_deletable(True)
            Gdk.flush()
        Gdk.threads_add_idle(GLib.PRIORITY_HIGH, _window_set_deletable)
        if returncode:
            self.vte_set_status(_("Error during installation"))
        else:
            if auto_close:
                self.close()
            else:
                self.vte_set_status(_("Installation complete"))
        return returncode
