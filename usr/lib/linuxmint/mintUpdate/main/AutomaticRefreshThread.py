import threading
import time
import traceback

from common import settings


class AutomaticRefreshThread(threading.Thread):

    def __init__(self, application):
        threading.Thread.__init__(self, daemon=True)
        self.application = application
        self.minute = 60
        self.hour = 60 * self.minute
        self.day = 24 * self.hour
        self.settings_prefix = ""
        self.refresh_type = "initial"
        self.initial_refresh = True
        self.refresh_last_run = int(time.time())

    def run(self):
        self.application.logger.write("AutomaticRefreshThread started")
        while self.application.refresh_schedule_enabled:
            last_timetosleep = 0
            try:
                # Always wait one minute regardless of the schedule
                time.sleep(60)

                # Check the schedule
                timetosleep = self.get_scheduled_timetosleep()
                if not timetosleep:
                    self.application.logger.write(
                        f"{self.refresh_type.capitalize()} refresh schedule disabled in preferences, skipping")
                    if not self.initial_refresh:
                        break
                else:
                    timetosleep = self.get_remaining_timetosleep(timetosleep)

                    # Refresh the schedule every 15 minutes
                    loop_time = 15 * self.minute
                    while self.application.refresh_schedule_enabled and timetosleep > 0:
                        # Write a log message about the schedule if it changed +/- 2.5 minutes
                        if not (last_timetosleep - 2.5 * self.minute < timetosleep + loop_time <
                                last_timetosleep + 2.5 * self.minute):
                            self.log_schedule(timetosleep)
                        if timetosleep > loop_time:
                            time.sleep(loop_time)
                            last_timetosleep = timetosleep
                            timetosleep = self.get_scheduled_timetosleep()
                            if not timetosleep:
                                # Schedule changed to 0 in preferences, break here
                                timetosleep = -1
                                break
                            timetosleep = self.get_remaining_timetosleep(timetosleep)
                        else:
                            time.sleep(timetosleep)
                            timetosleep = 0
                    if not self.application.refresh_schedule_enabled:
                        self.application.logger.write(
                            f"Auto-refresh disabled in preferences, canceling {self.refresh_type} refresh")
                        break
                    if not timetosleep < 0:
                        if self.application.app_hidden:
                            self.application.logger.write(
                                f"Triggering {self.refresh_type} refresh")
                            refresh = self.application.refresh(True)
                            if refresh:
                                while refresh.is_alive():
                                    time.sleep(5)
                            del refresh
                        else:
                            self.application.logger.write(
                                f"Update Manager window is open, delaying {self.refresh_type} refresh by 60s")
                            continue
            except:
                self.application.logger.write_error(
                    f"Exception occurred during {self.refresh_type} refresh:\n{traceback.format_exc()}")

            if self.initial_refresh:
                self.initial_refresh = False
                self.settings_prefix = "auto"
                self.refresh_type = "recurring"
        else:
            self.application.logger.write(f"Auto-refresh disabled in preferences")
        self.application.auto_refresh = None

    def __del__(self):
        self.application.logger.write(f"AutomaticRefreshThread stopped")

    def log_schedule(self, timetosleep):
        days = int(timetosleep / self.day)
        hours = int((timetosleep - days * self.day) / self.hour)
        minutes = int((timetosleep - days * self.day - hours * self.hour) / self.minute)
        self.application.logger.write(
            f"{self.refresh_type.capitalize()} refresh will happen in {days} day(s), {hours} hour(s) and {minutes} minute(s)")

    def get_scheduled_timetosleep(self):
        return settings.get_int(f"{self.settings_prefix}refresh-minutes") * self.minute + \
               settings.get_int(f"{self.settings_prefix}refresh-hours") * self.hour + \
               settings.get_int(f"{self.settings_prefix}refresh-days") * self.day

    def get_remaining_timetosleep(self, timetosleep):
        now = int(time.time())
        if not self.initial_refresh:
            refresh_last_run = settings.get_int64("refresh-last-run")
            if refresh_last_run != self.refresh_last_run:
                self.refresh_last_run = refresh_last_run
            if not refresh_last_run or refresh_last_run > now:
                self.refresh_last_run = now
                settings.set_int64("refresh-last-run", now)
        time_since_last_refresh = now - self.refresh_last_run
        if time_since_last_refresh > 0:
            timetosleep = timetosleep - time_since_last_refresh
        if timetosleep < 0:
            timetosleep = 0
        return timetosleep
