import os
import tempfile
import traceback
from datetime import datetime

import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib


class Logger:

    def __init__(self, prefix):
        self.prefix = prefix
        self.logdir = os.path.join(tempfile.gettempdir(), "mintUpdate/")
        self._create_log()
        self.hook = None

    def _create_log(self):
        if not os.path.exists(self.logdir):
            os.umask(0)
            os.makedirs(self.logdir)
        self.log = tempfile.NamedTemporaryFile(mode="w",
            prefix=f"{self.prefix}_{datetime.now().strftime('%Y-%m-%d_%H:%M')}_",
            suffix=".log", dir=self.logdir, delete=False)
        try:
            os.chmod(self.log.name, 0o666)
        except:
            self.write_error(f"Exception in Logger setting permissions of {self.log.name}:\n{traceback.format_exc()}")

    def _log_ready(self):
        if self.log.closed:
            return False
        if not os.path.exists(self.log.name):
            self.log.close()
            self._create_log()
        return True

    def _write(self, line):
        if self._log_ready():
            self.log.write(line)
            self.log.flush()
        if self.hook:
            try:
                Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT, self.hook, line)
            except:
                pass

    def write(self, line):
        self._write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} + {line}\n")

    def write_error(self, line):
        self._write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} - {line}\n")

    def read(self):
        if not os.path.exists(self.log.name):
            self._create_log()
            return ""
        else:
            with open(self.log.name) as f:
                return f.read()

    def close(self):
        self.log.close()

    def set_hook(self, callback):
        """ Configure a callback function for the Logger to echo log lines to (thread safe) """
        self.hook = callback

    def remove_hook(self):
        self.hook = None
