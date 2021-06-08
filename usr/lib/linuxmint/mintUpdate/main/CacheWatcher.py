import os
import threading
import time

import apt_pkg

from common.functions import dpkg_locked


class CacheWatcher(threading.Thread):
    """
    Monitors package cache and dpkg status and runs RefreshThread() on change.
    We use this instead of dpkg/apt post-invoke hooks because this must run
    under the user's context
    """

    def __init__(self, application, refresh_frequency=90):
        threading.Thread.__init__(self, daemon=True)
        self.application = application
        self.cachetime = 0
        self.pkgcache = None
        self.statustime = 0
        self.dpkgstatus = None
        self.paused = False
        self.refresh_frequency = refresh_frequency

    def run(self):
        if not self.pkgcache:
            apt_pkg.init_config()
            self.pkgcache = apt_pkg.config.find_file("Dir::Cache::pkgcache")
            self.dpkgstatus = apt_pkg.config.find_file("Dir::State::status")

        if not os.path.isfile(self.pkgcache) or not os.path.isfile(self.dpkgstatus):
            self.application.logger.write("Package cache location not found, disabling cache monitoring")
            self.pkgcache = None

        self.do_refresh()

        if self.pkgcache:
            self.update_cachetime()
            self.loop()

    def loop(self):
        while True:
            if not self.paused and self.application.window.get_sensitive():
                try:
                    cachetime = os.path.getmtime(self.pkgcache)
                    statustime = os.path.getmtime(self.dpkgstatus)
                    if (not cachetime == self.cachetime or \
                        not statustime == self.statustime) and \
                        not dpkg_locked():
                        self.cachetime = cachetime
                        self.statustime = statustime
                        self.refresh_cache()
                except:
                    pass
            time.sleep(self.refresh_frequency)

    def resume(self, update_cachetime=True):
        if not self.paused or not self.pkgcache:
            return
        if update_cachetime:
            self.update_cachetime()
        self.paused = False

    def pause(self):
        self.paused = True

    def update_cachetime(self):
        if self.pkgcache and os.path.isfile(self.pkgcache):
            self.cachetime = os.path.getmtime(self.pkgcache)
            self.statustime = os.path.getmtime(self.dpkgstatus)

    def refresh_cache(self):
        self.application.logger.write("Changes to the package cache detected, triggering refresh")
        self.do_refresh()

    def do_refresh(self):
        self.application.refresh()
