import os
import threading

import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib

from common.constants import NAMED_PIPE
from common.functions import dpkg_locked


class PipeMonitor(threading.Thread):

    def __init__(self, application):
        threading.Thread.__init__(self, daemon=True)
        self.application = application
        self.do_monitor = True

    def __del__(self):
        try:
            os.unlink(NAMED_PIPE)
        except:
            pass

    def run(self):
        try:
            os.mkfifo(NAMED_PIPE)
        except FileExistsError:
            pass
        while self.do_monitor:
            with open(NAMED_PIPE) as pipe:
                for line in pipe:
                    line = line.strip()
                    if line == "show":
                        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.show_window)
                    elif line == "hide":
                        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.hide_main_window)
                    elif line == "show-kernels":
                        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.open_kernels)
                    elif line == "show-history":
                        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.open_history)
                    elif line == "show-preferences":
                        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.open_preferences)
                    elif line == "show-log":
                        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.application.open_information)
                    elif line == "quit":
                        self.do_monitor = False
                        break
                    elif line == "refresh":
                        self.application.refresh(True)
                    # elif line == "dpkg-post-invoke":
                    #     if not dpkg_locked():
                    #         self.application.logger.write("Changes to the package cache detected, triggering refresh")
                    #         self.application.refresh()

    def stop(self):
        self.do_monitor = False
        try:
            with open(NAMED_PIPE, "w") as f:
                f.write("quit")
        except:
            pass