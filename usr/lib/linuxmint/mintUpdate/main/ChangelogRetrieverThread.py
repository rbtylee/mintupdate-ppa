import threading

import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, GObject

from mintcommon import apt_changelog

from common.KernelVersion import KernelVersion
from common.MainlineKernels import MAINLINE_KERNEL_DATA
from main.constants import UPDATE_OBJ


class ChangelogRetrieverThread(threading.Thread):

    def __init__(self, package_update, treeview, callback):
        threading.Thread.__init__(self, daemon=True)
        self.apt_changelog = apt_changelog.AptChangelog()
        self.callback = callback
        self.treeview = treeview
        self.pkg = package_update

    def run(self):
        # Add a flag to the update object so the main application knows if the
        # thread is running and does not try to start another one
        self.pkg.retrieving_changelog = True
        if not self.pkg.changelog and self.pkg.package_names[0]:
            if self.pkg.origin == "ubuntu" and self.pkg.archive.startswith("mainline-"):
                try:
                    mainline_branch_id = int(self.pkg.archive.split("-")[-1])
                    kernel = KernelVersion(self.pkg.new_version)
                    changelog_url = MAINLINE_KERNEL_DATA[mainline_branch_id].changelog_url(kernel.version)
                    r = apt_changelog.requests.get(changelog_url, timeout=5)
                    if r.ok:
                        self.pkg.changelog = r.text
                except:
                    self.pkg.changelog = None
            else:
                self.pkg.changelog = self.apt_changelog.get_changelog(self.pkg.package_names[0])
        if not self.pkg.changelog:
            self.pkg.changelog = _("No changelog available")
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self._display_changelog)

    def _display_changelog(self):
        model, tree_iter = self.treeview.get_selection().get_selected()
        if tree_iter:
            package_update = model.get_value(tree_iter, UPDATE_OBJ)
            if self.pkg == package_update:
                self.callback(self.pkg.changelog)
            GObject.Value.unset(package_update)

    def __del__(self):
        GObject.Value.unset(self.pkg)
        del(self.pkg.retrieving_changelog)
