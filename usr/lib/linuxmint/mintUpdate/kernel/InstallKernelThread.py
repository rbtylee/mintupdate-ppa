import os
import subprocess
import tempfile
import threading

import apt

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

from common import settings
from common.constants import (KERNEL_PKG_NAMES, PKEXEC_ENV, ROOT_FUNCTIONS,
                              SUPPORTED_KERNEL_TYPES, Origin)
from common.MainlineKernelInstaller import MainlineKernelInstaller


class InstallKernelThread(threading.Thread):

    def __init__(self, kernels, application, kernel_window):
        threading.Thread.__init__(self, daemon=False)
        self.kernels = kernels
        self.application = application
        self.kernel_window = kernel_window
        self.cache = None

    def __del__(self):
        self.cache = None
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._GUI_finalize)
        self.application.refresh_inhibited = False

    def _GUI_finalize(self):
        if not self.kernel_window.is_standalone:
            self.kernel_window.destroy_window(None, refresh=True)
        else:
            self.kernel_window.status_installing_spinner.stop()
            self.kernel_window.window.set_sensitive(True)
            self.kernel_window.refresh_kernels_list()

    def run(self):
        self.application.refresh_inhibited = True
        self.application.cache_watcher.pause()
        self.application.logger.write("Starting kernel installation/removal")
        auto_close = settings.get_boolean("automatically-close-update-details")
        do_mainline = False
        do_regular = False
        for kernel in self.kernels:
            self.application.logger.write(
                f'Will {"remove" if kernel.installed else "install"} kernel linux-{kernel.version}{kernel.type}…')
            if not kernel.installed and kernel.origin == Origin.MAINLINE_PPA:
                mainline = MainlineKernelInstaller(transient_for=self.application.window)
                try:
                    mainline_kernel_files = [x["filename"] for x in mainline.get_filelist(kernel.version, False)]
                except mainline.MainlineKernelsException as e:
                    mainline.close()
                    self.show_error_msg(e.args[0])
                    break
                if not do_mainline:
                    debfiles = []
                try:
                    downloaded_files = mainline.download_files(kernel.version, mainline_kernel_files)
                except mainline.DownloadError as e:
                    mainline.close()
                    self.show_error_msg(e.args[0])
                    break
                if downloaded_files:
                    debfiles.extend(downloaded_files)
                    do_mainline = True
            else:
                if not do_regular:
                    do_regular = True
                    f = tempfile.NamedTemporaryFile()
                    window = self.application.window.get_window()
                    if window:
                        xid = str(window.get_xid())
                    else:
                        xid = ""
                    cmd = ["pkexec", ROOT_FUNCTIONS, "synaptic", xid, f.name, "closeZvt" * auto_close]
                    cmd.extend(PKEXEC_ENV)
                    if not self.cache:
                        self.cache = apt.Cache()
                _KERNEL_PKG_NAMES = KERNEL_PKG_NAMES.copy()
                if kernel.installed:
                    _KERNEL_PKG_NAMES.append("linux-image-unsigned-VERSION-KERNELTYPE") # mainline, remove only
                for name in _KERNEL_PKG_NAMES:
                    name = name.replace("VERSION", kernel.version).replace("-KERNELTYPE", kernel.type)
                    if name in self.cache:
                        pkg = self.cache[name]
                        if kernel.installed:
                            if pkg.is_installed:
                                # skip kernel_type independent packages (headers) if another kernel of the
                                # same version but different type is installed
                                if not kernel.type in name and \
                                   self.package_needed_by_another_kernel(kernel.version, kernel.type):
                                    continue
                                pkg_line = f"{name}\tpurge\n"
                                f.write(pkg_line.encode("utf-8"))
                        else:
                            pkg_line = f"{name}\tinstall\n"
                            f.write(pkg_line.encode("utf-8"))

                # Clean out left-over meta package
                if kernel.installed:
                    last_in_series = True
                    this_kernel_series = self.kernel_series(kernel.version)
                    for _type, _version in self.kernel_window.installed_kernels:
                        if _type == kernel.type and _version != kernel.version and \
                           self.kernel_series(_version) == this_kernel_series and not \
                           [True for k in self.kernels if k.type == _type and k.version == _version]:
                            # We could also compare origin here but better to
                            # err on the safe side here and leave a meta behind
                            last_in_series = False
                    if last_in_series:
                        meta_names = []
                        _metas = [s for s in self.cache.keys() if s.startswith("linux" + kernel.type)]
                        if kernel.type == "-generic":
                            _metas.append("linux-virtual")
                        elif kernel.type == "-liquorix":
                            # The Liquorix PPA doesn't include a linux-liquorix meta unfortunately
                            _metas.append("linux-headers-liquorix-amd64")
                            _metas.append("linux-image-liquorix-amd64")
                        for meta in _metas:
                            shortname = meta.split(":")[0]
                            if shortname not in meta_names:
                                meta_names.append(shortname)
                        for meta_name in meta_names:
                            if meta_name in self.cache:
                                meta = self.cache[meta_name]
                                if meta.is_installed and \
                                   self.kernel_series(meta.candidate.version) == this_kernel_series:
                                    self.application.logger.write(
                                        f'Will remove meta-package {meta_name}…')
                                    f.write(("%s\tpurge\n" % meta_name).encode("utf-8"))
                                    if kernel.type == "-liquorix":
                                        continue
                                    f.write(("%s\tpurge\n" % meta_name.replace("linux-","linux-image-")).encode("utf-8"))
                                    f.write(("%s\tpurge\n" % meta_name.replace("linux-","linux-headers-")).encode("utf-8"))
                                    if meta_name == "linux-virtual":
                                        f.write(("linux-headers-generic\tpurge\n").encode("utf-8"))
                f.flush()

        success = True
        if do_regular or (do_mainline and debfiles):
            if do_regular:
                try:
                    result = subprocess.run(cmd, stdout=self.application.logger.log,
                                            stderr=self.application.logger.log, check=True)
                    returncode = result.returncode
                except subprocess.CalledProcessError as e:
                    returncode = e.returncode
                f.close()
                self.application.logger.write(f"Synaptic return code: {returncode}")
                if returncode:
                    success = False
            if do_mainline:
                returncode = mainline.install(debfiles, auto_close=auto_close)
                for filename in debfiles:
                    if os.path.isfile(filename):
                        os.remove(filename)
                self.application.logger.write(f"dpkg return code: {returncode}")
                if returncode:
                    success = False

    @staticmethod
    def kernel_series(version):
        return version.replace("-", ".").split(".")[:3]

    def package_needed_by_another_kernel(self, version, current_kernel_type):
        for kernel_type in SUPPORTED_KERNEL_TYPES:
            if kernel_type == current_kernel_type:
                continue
            for name in KERNEL_PKG_NAMES:
                if "-KERNELTYPE" in name:
                    name = name.replace("VERSION", version).replace("-KERNELTYPE", kernel_type)
                    if name in self.cache:
                        pkg = self.cache[name]
                        if pkg.is_installed:
                            return True
        return False

    def show_error_msg(self, msg):
        self.application.logger.write(msg)
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self._show_error_dialog, msg)

    def _show_error_dialog(self, msg):
        dialog = Gtk.MessageDialog(transient_for=self.application.window,
                                modal=True,
                                destroy_with_parent=True,
                                message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.OK,
                                title=_("Cannot Proceed"),
                                text=msg)
        dialog.run()
        dialog.destroy()
