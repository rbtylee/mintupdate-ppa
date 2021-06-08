#!/usr/bin/python3

import argparse
import fnmatch
import os
import subprocess
import sys
import traceback

from checkAPT import APTCheck
from common.constants import (PRIORITY_UPDATES, REBOOT_REQUIRED_FILE,
                              ROOT_FUNCTIONS, UPDATE_FAILED_FILE)
from common.functions import check_timeshift, read_file

if __name__ == "__main__":
    failed = False
    def is_blacklisted(blacklisted_packages, name, version):
        for blacklist in blacklisted_packages:
            if "=" in blacklist:
                (bl_pkg, bl_ver) = blacklist.split("=", 1)
            else:
                bl_pkg = blacklist
                bl_ver = None
            if fnmatch.fnmatch(name, bl_pkg) and (not bl_ver or bl_ver == version):
                return True
        return False

    parser = argparse.ArgumentParser(prog="mintupdate-cli")
    parser.add_argument("command", choices=["list", "upgrade"], nargs='?',
        help="Command to run")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-k", "--only-kernel", action="store_true",
        help="Only apply kernel updates")
    group.add_argument("-s", "--only-security", action="store_true",
        help="Only apply security updates")
    parser.add_argument("-m", "--mainline", action="store_true",
        help="Enable mainline kernel upgrades (use with caution)")
    parser.add_argument("-i", "--ignore",
        help="List of updates to ignore (comma-separated list of source package "
             "names). You can also blacklist updates by adding them to "
             "/etc/mintupdate.blacklist, one source package per line. "
             "To ignore a specific version, use the format package=version.")
    parser.add_argument("-r", "--refresh-cache", action="store_true",
        help="Refresh the APT cache first")
    parser.add_argument("-d", "--dry-run", action="store_true",
        help="Simulation mode, don't upgrade anything")
    parser.add_argument("-y", "--yes", action="store_true",
        help="Automatically answer yes to all questions and always install new "
             "configuration files (unless you also use the --keep-configuration "
             "option)")
    parser.add_argument("--quiet", action="store_true",
        help="Produce only minimal output. Implies --yes.")
    parser.add_argument("--install-recommends", action="store_true",
        help="Install recommended packages (use with caution)")
    parser.add_argument("--keep-configuration", action="store_true", default=False,
        help="Always keep local changes in configuration files (use with caution)")
    parser.add_argument("-t", "--create-snapshot", action="store_true",
        help="Create system snapshot with timeshift before installing updates")
    parser.add_argument("-v", "--version", action="version", version="__DEB_VERSION__",
        help="Display the current version")
    args = parser.parse_args()

    # Show help if no command
    if not args.command:
        parser.print_help()
        sys.exit()

    # Reload with sudo if not root
    uid = os.getuid()
    if os.getuid() != 0 and args.command == "upgrade":
        p = subprocess.run(["sudo", "/usr/bin/mintupdate-cli"] + sys.argv[1:])
        sys.exit(p.returncode)

    try:
        if args.refresh_cache:
            cmd = ["/usr/bin/mint-refresh-cache", "--mintupdate"]
            if not uid == 0:
                cmd.insert(0, "sudo")
            subprocess.run(cmd)
        check = APTCheck(args.mainline)
        check.find_changes()

        blacklisted = []
        for line in read_file("/etc/mintupdate.blacklist"):
            line = line.strip()
            if line.startswith("#"):
                continue
            blacklisted.append(line)
        if args.ignore:
            blacklisted.extend(args.ignore.split(","))

        updates = []
        mainline_updates = {}
        for source_name in sorted(check.updates.keys()):
            update = check.updates[source_name]
            if source_name in PRIORITY_UPDATES:
                updates.append(update)
            elif args.only_kernel and update.type != "kernel":
                continue
            elif args.only_security and update.type != "security":
                continue
            elif is_blacklisted(blacklisted, update.real_source_name, update.new_version):
                continue
            elif update.archive.startswith("mainline-"):
                mainline_branch_id = int(update.archive.split("-")[-1])
                if not mainline_branch_id in mainline_updates:
                    mainline_updates[mainline_branch_id] = []
                mainline_updates[mainline_branch_id].append(update)
            else:
                updates.append(update)

        if args.command == "list":
            for update in updates:
                print("%-15s %-45s %s" % (update.type, update.source_name, update.new_version), flush=True)
            for mainline_branch_id in mainline_updates:
                for mainline_update in mainline_updates[mainline_branch_id]:
                    print("%-15s %-45s %s" % (update.type, update.source_name, update.new_version), flush=True)
        elif args.command == "upgrade":
            packages = []
            for update in updates:
                packages += update.package_names
            arguments = ["apt-get", "install"]
            if args.dry_run:
                arguments.append("--simulate")
            if args.yes or args.quiet:
                environment = os.environ
                environment.update({"DEBIAN_FRONTEND": "noninteractive"})
                arguments.append("--assume-yes")
                if args.quiet:
                    arguments.append("--quiet=2")
                if not args.keep_configuration:
                    arguments.extend(["--option", "Dpkg::Options::=--force-confnew"])
            else:
                environment = None
            if args.install_recommends:
                arguments.append("--install-recommends")
            if args.keep_configuration:
                arguments.extend(["--option", "Dpkg::Options::=--force-confold"])

            # Create system snapshot
            if args.create_snapshot and (packages or mainline_updates):
                if not check_timeshift():
                    # Abort if Timeshift is missing/not configured
                    print("Timeshift missing or not configured correctly.\n"
                          "Update installation aborted.", flush=True)
                    sys.exit(1)
                # Assemble snapshot comment
                package_names = packages.copy()
                if mainline_updates:
                    for mainline_branch_id in mainline_updates:
                        for update in mainline_updates[mainline_branch_id]:
                            package_names.extend(update.package_names)
                comment = _("Before updating: %s") % f'{", ".join(package_names)} #mintupdate'

                cmd = ["pkexec", ROOT_FUNCTIONS, "timeshift", comment]
                try:
                    print("Creating system snapshot...", flush=True)
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, encoding="utf-8", bufsize=1)
                    failed = False
                    snapshot_done = False
                    error = None
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
                            continue
                        # Detect when pruning begins and change status accordingly
                        elif not snapshot_done and "#mintupdate-pruning-snapshots" in line:
                            snapshot_done = True
                            continue
                    p.wait()
                    if p.returncode or failed:
                        raise subprocess.CalledProcessError(p.returncode, cmd, error)
                    print("System snapshot completed successfully", flush=True)
                except subprocess.CalledProcessError as e:
                    print(f"System snapshot failed: {e.stdout}", flush=True)
                    # Abort if snapshot failed
                    print("Update installation aborted", flush=True)
                    sys.exit(1)

            # Install updates
            if packages:
                try:
                    subprocess.run(arguments + packages, env=environment, check=True)
                    if not os.path.exists(REBOOT_REQUIRED_FILE):
                        if [True for update in updates if update.type == "kernel"]:
                            open(REBOOT_REQUIRED_FILE, "w").close()
                    if os.path.exists(UPDATE_FAILED_FILE):
                        os.unlink(UPDATE_FAILED_FILE)
                except subprocess.CalledProcessError:
                    print(f"Failed to upgrade: {packages}", flush=True)
                    failed = True

            # Install mainline kernel update
            if mainline_updates:
                from common.MainlineKernels import MainlineKernels

                for mainline_branch_id in mainline_updates:
                    mainline = MainlineKernels(branch_id=mainline_branch_id)
                    # Download
                    for mainline_update in mainline_updates[mainline_branch_id]:
                        # versioned builds
                        if mainline_branch_id == 0:
                            mainline_version = mainline_update.new_version.split("-", 1)[0]
                        # daily builds
                        else:
                            mainline_version = mainline_update.new_version
                        if args.dry_run:
                            print(f"Installing mainline kernel {mainline_version}", flush=True)
                        else:
                            try:
                                print(f"Downloading mainline kernel {mainline_version}", flush=True)
                                downloaded_files = mainline.download_files(
                                    mainline_version, mainline_update.package_names)
                            except mainline.DownloadError as e:
                                print("Download error:", flush=True)
                                traceback.print_exc()
                                downloaded_files = None
                # Install
                if args.dry_run:
                    pass
                elif downloaded_files:
                    retval = mainline.install(downloaded_files, is_upgrade=True)
                    if retval:
                        failed = True
                    for filename in downloaded_files:
                        os.remove(filename)
                else:
                    failed = True
    except SystemExit:
        failed = True
    except:
        traceback.print_exc()
        failed = True

    if failed:
        sys.exit(1)
