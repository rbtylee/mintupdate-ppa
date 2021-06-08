#!/usr/bin/python3

import fnmatch
import os
import re
import sys
from datetime import datetime, timedelta

import apt

from common import settings
from common.constants import (KERNEL_PKG_NAMES, PRIORITY_UPDATES,
                              SUPPORTED_KERNEL_TYPES, USE_MAINLINE_KERNELS)
from common.functions import configured_kernel_type
from common.KernelVersion import KernelVersion
from common.MainlineKernels import MainlineKernels
from main.Update import Update


def get_origins(package):
    package_origins = set()
    for version in package.versions:
        for origin in version.origins:
            if origin.origin:
                package_origins.add(origin.origin)
    return package_origins

class Meta:

    def __init__(self, package_name, package):
        self.name = package_name
        self.package = package
        self.origins = get_origins(package)

class APTCheck:

    def __init__(self, use_mainline=None):
        if not use_mainline == None:
            self.use_mainline = use_mainline
            self.mainline_upgrade_series = True
        else:
            self.use_mainline = USE_MAINLINE_KERNELS
            self.mainline_upgrade_series = settings.get_boolean("mainline-upgrade-eol-series")
        self.cache = apt.Cache()
        self.priority_updates_available = False
        self.updates = {}
        self.configured_kernel_type = configured_kernel_type()
        self.metas = {}

    def find_changes(self):
        self.cache.upgrade(True) # dist-upgrade
        changes = self.cache.get_changes()

        self.updates.clear()

        ### Package updates:
        for pkg in changes:
            if pkg.is_installed and pkg.marked_upgrade and pkg.candidate.version != pkg.installed.version:
                self.add_update(pkg)

        # Stop here if we have priority updates - which kernels never are
        if self.priority_updates_available:
            return

        ### Kernel updates:

        # Get the uname version
        active_kernel = KernelVersion(os.uname().release)

        # Uncomment for testing, pass --debug parameter on command line or set this:
        # global DEBUG
        # DEBUG = True
        # active_kernel = KernelVersion("4.15.0-20-generic")
        # active_kernel = KernelVersion("4.18.0-24-generic")
        # active_kernel = KernelVersion("5.1.0-050100-generic")
        # active_kernel = KernelVersion("5.3.0-050300-generic")
        # active_kernel = KernelVersion("5.4.0-050400-generic")
        # active_kernel = KernelVersion("5.3.0-999")
        # self.use_mainline = True
        # self.mainline_upgrade_series = True
        # self.mainline_upgrade_series = False

        # Get available meta-packages
        active_kernel_pkg_name = next((s for s in self.cache.keys() \
            if s.startswith("linux-image-") and s.endswith(active_kernel.version)), "")
        active_kernel_pkg = self.cache.get(active_kernel_pkg_name)
        if active_kernel_pkg:
            active_kernel_origins = get_origins(active_kernel_pkg)
        else:
            active_kernel_origins = set()
        lts_meta_name = "linux" + self.configured_kernel_type
        meta_names = [s for s in self.cache.keys() if s.startswith(lts_meta_name)]
        meta_names.sort()
        if self.configured_kernel_type == "-generic" and "Ubuntu" in active_kernel_origins:
            meta_names.append("linux-virtual")
        elif self.configured_kernel_type == "-liquorix":
            # The Liquorix PPA doesn't include a linux-liquorix meta unfortunately
            meta_names.append("linux-headers-liquorix-amd64")
            meta_names.append("linux-image-liquorix-amd64")
        for meta_name in meta_names:
            meta_name = meta_name.split(":")[0]
            if not meta_name in self.metas:
                meta_pkg = self.cache.get(meta_name)
                if meta_pkg:
                    self.metas[meta_name] = Meta(meta_name, meta_pkg)

        # Override installed kernel if not of the configured type
        try:
            active_kernel_type = "-" + active_kernel.version.split("-")[-1]
        except:
            active_kernel_type = self.configured_kernel_type
        if  active_kernel_type != self.configured_kernel_type:
            active_kernel.series = ("0", "0", "0")

        # Check if any meta is installed..
        meta_candidate_same_series = None
        meta_candidate_higher_series = None
        for meta_name, meta in self.metas.items():
            if not active_kernel_origins.intersection(meta.origins):
                # Meta package shares no origin with the active kernel, ignore
                continue
            meta_kernel = KernelVersion(meta.package.candidate.version)
            if active_kernel.series > meta_kernel.series:
                # Meta is lower than the active kernel series, ignore
                continue
            else:
                # Meta is higher or same as active kernel series:
                if meta.package.is_installed:
                    # Meta is already installed, return
                    return
                # never install linux-virtual, we only support it if installed
                if meta_name == "linux-virtual":
                    continue
                # Meta is not installed, make it a candidate if higher than any
                # current candidate
                if active_kernel.series == meta_kernel.series:
                    # same series
                    if not meta_candidate_same_series or meta_kernel.version_id > \
                        KernelVersion(meta_candidate_same_series.candidate.version).version_id:
                        meta_candidate_same_series = meta.package
                else:
                    # higher series
                    if meta_candidate_higher_series:
                        meta_candidate_version = KernelVersion(meta_candidate_higher_series.candidate.version)
                    # use meta with lowest highest series with highest version
                    if not meta_candidate_higher_series or \
                       meta_kernel.series < meta_candidate_version.series or \
                       (meta_kernel.series == meta_candidate_version.series and \
                       meta_kernel.version_id > meta_candidate_version.version_id):
                        meta_candidate_higher_series = meta.package

        # If we're here, no meta was installed
        if self.configured_kernel_type == "-liquorix":
            # Since the Liquorix PPA has no proper meta, we need to work around this:
            for meta in self.metas:
                self.add_update(meta, kernel_update=True)
            return
        if meta_candidate_same_series:
            # but a candidate of the same series was found, add to updates and return
            self.add_update(meta_candidate_same_series, kernel_update=True)
            return

        # If we're here, no matching meta was found
        if meta_candidate_higher_series:
            # but we found a higher meta candidate, add it to the list of updates
            # unless the installed kernel series is lower than the LTS series
            # for some reason, in the latter case force the LTS meta
            if meta_candidate_higher_series.name != lts_meta_name and lts_meta_name in self.cache:
                lts_meta = self.cache.get(lts_meta_name)
                lts_meta_kernel = KernelVersion(lts_meta.candidate.version)
                if active_kernel.series < lts_meta_kernel.series:
                    meta_candidate_higher_series = lts_meta
            self.add_update(meta_candidate_higher_series, kernel_update=True)
            return

        # We've gone past all the metas, so we should recommend the latest
        # kernel on the series we're in
        max_kernel = active_kernel
        for pkgname in self.cache.keys():
            match = re.match(rf'^(?:linux-image-)(\d.+?){active_kernel_type}$', pkgname)
            if match:
                kernel = KernelVersion(match.group(1))
                if kernel.series == max_kernel.series and kernel.version_id > max_kernel.version_id:
                    max_kernel = kernel
        if max_kernel.version_id != active_kernel.version_id:
            _upgrade_added = False
            for pkgname in KERNEL_PKG_NAMES:
                pkgname = pkgname.replace('VERSION', max_kernel.version).replace("-KERNELTYPE", active_kernel_type)
                pkg = self.cache.get(pkgname)
                if pkg and not pkg.is_installed:
                    _upgrade_added = True
                    self.add_update(pkg, kernel_update=True)
            if _upgrade_added:
                return

        # check mainline kernels, if enabled
        if self.use_mainline:
            if active_kernel.version_id[3].isnumeric():
                # Daily builds like "5.2.0-999-generic"
                mainline_branch_id = int(active_kernel.version_id[3])
            else:
                # Regular versioned builds
                mainline_branch_id = 0
            # since all we got is the active kernel version, try to make sure there's no
            # signed, i.e. non-mainline package of the same version installed:
            pkg = self.cache.get(f"linux-image-{active_kernel.version}")
            if not DEBUG and pkg and pkg.is_installed:
                return
            # and that an unsigned, local version is installed instead:
            pkg = self.cache.get(f"linux-image-unsigned-{active_kernel.version}")
            if not DEBUG and (not pkg or not pkg.is_installed or pkg.candidate.downloadable):
                return
            max_kernel = active_kernel
            try:
                mainline = MainlineKernels(cached=True, branch_id=mainline_branch_id)
            except:
                print("E: Unhandled kernel type installed")
                return
            is_eol = False
            is_rc = False
            try:
                # regular versioned builds:
                if mainline_branch_id == 0:
                    ver = ".".join(active_kernel.version.split(".")[:2])
                    mainline_support_status = mainline.get_support_status()
                    if not ver in mainline_support_status or mainline_support_status[ver] == "eol":
                        is_eol = True
                    # if the active kernel is not eol, check if it is a release candidate
                    is_rc = not is_eol and "rc" in active_kernel.version_id[3]
                    mainline.include_rc = is_rc
                    mainline_kernels = mainline.get_available_versions(
                        filter_eol=not (is_eol or self.mainline_upgrade_series),
                        filter_rc=not is_rc,
                        filter_longterm=False)
                # dated (daily) builds:
                else:
                    mainline_kernels = [mainline.get_daily_build()]
                if not mainline_kernels:
                    print("E: Could not retrieve available mainline kernel versions")
                    return
            except:
                print("E: Exception trying to retrieve available mainline kernel versions")
                return
            if DEBUG:
                print(mainline_kernels)
                print("active_kernel.version:", active_kernel.version)
                print("mainline_branch_id:", mainline_branch_id)
                print("is_eol:", is_eol, ", filter_eol:", not (is_eol or self.mainline_upgrade_series))
                print("is_rc:", is_rc, ", filter_rc:", not is_rc)
                print("self.mainline_upgrade_series:", self.mainline_upgrade_series)
            # Versioned builds:
            if mainline_branch_id == 0:
                # get the highest available mainline kernel version matching the
                # current shortseries, or, if the current series is end of life
                # and series upgrades are enabled, the highest available mainline
                # kernel from the highest released series:
                target_kernel_version = ""
                for mainline_kernel in mainline_kernels:
                    kernel = KernelVersion(mainline_kernel)
                    if ((is_eol and self.mainline_upgrade_series) or
                        kernel.shortseries == max_kernel.shortseries) and \
                       kernel.version_id > max_kernel.version_id:
                        max_kernel = kernel
                        target_kernel_version = mainline_kernel
                if max_kernel.version_id != active_kernel.version_id:
                    try:
                        mainline_kernel_files = mainline.get_filelist(target_kernel_version)
                    except mainline.MainlineKernelsException as e:
                        print(e)
                        return
                    package_version = mainline_kernel_files[0]["filename"].split("_")[0].split("-", 2)[-1]
                    # Check if blacklisted
                    if self.is_blacklisted("linux", package_version):
                        return
                    # Check if already installed but not active (yet):
                    pkg = self.cache.get(f"linux-image-unsigned-{package_version}")
                    if pkg and pkg.is_installed:
                        return
                    base_url = f"{mainline.base_data.base_url}v{target_kernel_version}/"
                else:
                    return
            # Dated (daily) builds:
            else:
                target_kernel_version = mainline_kernels[0]
                active_build_date = os.uname().version[1:9]
                # The source version is one day older than the actual build date, so we
                # substract a day to be able to compare with the installed build
                latest_build_date = KernelVersion(target_kernel_version).version_id[3]
                latest_build_date = datetime.strptime(latest_build_date, "%Y%m%dz")
                latest_build_date = latest_build_date - timedelta(days=1)
                latest_build_date = latest_build_date.strftime("%Y%m%d")
                # Check if we're already on the latest build:
                if latest_build_date == active_build_date:
                    return
                try:
                    mainline_kernel_files = mainline.get_filelist(target_kernel_version)
                except mainline.MainlineKernelsException as e:
                    print(e)
                    return
                package_version = mainline_kernel_files[0]["filename"].split("_")[0].split("-", 2)[-1]
                if not DEBUG:
                    pkg = self.cache.get(f"linux-image-unsigned-{package_version}")
                    # Double check that a daily build is really already installed,
                    # we are trying to update after all. Also check blacklist.
                    if not pkg or not pkg.installed or self.is_blacklisted("linux", package_version):
                        return
                    # Check whether we already installed the latest version and it's just not active (yet):
                    installed_build_date = pkg.installed.version.split(f".0-{mainline_branch_id}.", 1)[1][:8]
                    if installed_build_date == latest_build_date:
                        return
                base_url = mainline.base_data.versioned_url(target_kernel_version)
            download_size = sum([x["size"] for x in mainline_kernel_files])
            # we cannot know installed size before downloading, at best we could offer an guess here
            installed_size = 0
            installed_size_change = 0
            mainline_update = \
                "###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s###%s" %\
                (f"Linux kernel {package_version}", "linux", "linux",
                f"linux={package_version}", mainline_kernel_files[2]["filename"],
                ", ".join([x["filename"] for x in mainline_kernel_files]),
                target_kernel_version, active_kernel.version,
                download_size, installed_size, installed_size_change,
                "kernel", "ubuntu", mainline.base_data.title,
                _("Warning: This is an unsupported kernel meant for testing purposes. "
                "Consider switching to a supported kernel instead."),
                base_url, f"mainline-{mainline.base_data.name}-{mainline_branch_id}")
            self.updates["linux"] = Update(package=None, input_string=mainline_update, source_name=None)

    def is_blacklisted(self, source_name, version):
        for blacklist in settings.get_strv("blacklisted-packages"):
            if "=" in blacklist:
                bl_pkg, bl_ver = blacklist.split("=", 1)
            else:
                bl_pkg = blacklist
                bl_ver = None
            if fnmatch.fnmatch(source_name, bl_pkg) and (not bl_ver or bl_ver == version):
                return True
        return False

    def get_kernel_version_from_meta_package(self, pkg):
        for dependency in pkg.dependencies:
            if not dependency.target_versions or not dependency.rawtype == "Depends":
                return None
            try:
                deppkg = self.cache[dependency.target_versions[0].package.name].candidate
            except:
                return None
            if deppkg.source_name in ("linux", "linux-signed"):
                return deppkg.source_version
            if deppkg.source_name.startswith("linux-meta"):
                return self.get_kernel_version_from_meta_package(deppkg)
        return None

    def add_update(self, package, kernel_update=False):
        source_version = package.candidate.version
        # Change version of kernel meta packages to that of the actual kernel
        # for grouping with related updates
        if package.candidate.source_name.startswith("linux-meta"):
            _source_version = self.get_kernel_version_from_meta_package(package.candidate)
            if _source_version:
                source_version = _source_version

        # Change source name of kernel packages for grouping with related updates
        if (package.candidate.source_name == "linux" or
            package.candidate.source_name.startswith("linux-hwe") or
            package.candidate.source_name.startswith("linux-meta") or
            package.candidate.source_name.startswith("linux-signed") or
            [True for flavor in SUPPORTED_KERNEL_TYPES if package.candidate.source_name.startswith(f"linux{flavor}")]
           ):
            kernel_update = True
            source_name = f"linux-{source_version}"
        else:
            source_name = package.candidate.source_name

        # ignore packages blacklisted by the user
        if self.is_blacklisted(package.candidate.source_name, package.candidate.version):
            return

        if not self.priority_updates_available and source_name in PRIORITY_UPDATES:
            self.updates.clear()
            self.priority_updates_available = True
        if not self.priority_updates_available or source_name in PRIORITY_UPDATES:
            if source_name in self.updates:
                update = self.updates[source_name]
                update.add_package(package)
                # Adjust update.old_version for kernel updates to try and
                # match the kernel, not the meta
                if kernel_update and package.is_installed and not \
                   "-" in update.old_version and "-" in package.installed.version:
                    update.old_version = package.installed.version
            else:
                update = Update(package, source_name=source_name)
                self.updates[source_name] = update
            if kernel_update:
                update.type = "kernel"
            update.new_version = source_version

    def serialize_updates(self):
        # Print updates
        for _source_name, update in self.updates.items():
            update.serialize()

    def merge_kernel_updates(self):
        for source_name, update in self.updates.items():
            if update.type == "kernel" and not update.archive.startswith("mainline-") and \
               source_name not in ['linux-libc-dev', 'linux-kernel-generic'] and \
               (len(update.package_names) >= 3 or update.package_names[0] in self.metas):
                update.display_name = _("Linux kernel %s") % update.new_version
                update.short_description = _("The Linux kernel.")
                update.description = "%s\n\n%s\n\n%s\n\n%s" % (
                    # IMPORTANT: The first three labels are also used in kernels.ui's info_box, so keep them in sync
                    _("The Linux kernel provides the interface that allows your software to interact with your "
                    "hardware. It includes open-source device and filesystem drivers, and proprietary drivers "
                    "need to support it. Because of that, a new kernel can affect all areas of your system."),
                    _("Should you experience problems after installing or updating a kernel, you can reboot using "
                    "another kernel. This can be done via the corresponding function in Kernel Manager's options "
                    "menu, or via the GRUB boot loader's boot menu (access it by rebooting and pressing Esc once "
                    "after the BIOS boot screen disappears, then select the advanced options to access your previous "
                    "kernel). After the reboot, remove the problematic kernel using Kernel Manager."),
                    _("If you configured system snapshots, you can also use Timeshift to restore a previous known "
                    "working configuration."),
                    _("Be aware that this kernel update will be installed in addition to your current kernel. If you "
                    "do not have Update Manager's Automatic Maintenance service enabled, be sure to manage the amount "
                    "of kernels you keep installed for they take up a lot of disk space.")
                    )

    def clean_descriptions(self):
        for _source_name, update in self.updates.items():
            update.short_description = update.short_description.split("\n", 1)[0].capitalize()
            if update.short_description.endswith("."):
                update.short_description = update.short_description[:-1]

if __name__ == "__main__":
    DEBUG = len(sys.argv) > 1 and sys.argv[1] == "--debug"
    try:
        sys.stderr.close()
        check = APTCheck()
        check.find_changes()
        check.merge_kernel_updates()
        check.clean_descriptions()
        check.serialize_updates()
    except Exception as error:
        print("CHECK_APT_ERROR---EOL---")
        print(sys.exc_info()[0])
        print(f"Error: {error}")
        sys.exit(1)
