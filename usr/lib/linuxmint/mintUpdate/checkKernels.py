#!/usr/bin/python3

import os
import re
import sys

import apt

from common.constants import (SUPPORTED_KERNEL_TYPES, USE_MAINLINE_KERNELS,
                              Origin)
from common.functions import configured_kernel_type, get_release_dates
from common.KernelVersion import KernelVersion
from common.MainlineKernels import MainlineKernels

if len(sys.argv) > 1 and sys.argv[1] in SUPPORTED_KERNEL_TYPES:
    default_kernel_type = sys.argv[1]
else:
    default_kernel_type = configured_kernel_type()
release_dates = get_release_dates()

sys.stderr.close()
try:
    current_version = os.uname().release
    cache = apt.Cache()
    signed_kernels = ['']
    local_kernels = {}
    r = re.compile(r'^(?:linux-image-)(?:unsigned-)?(\d.+?)(%s)(|-amd64)$' % "|".join(SUPPORTED_KERNEL_TYPES))
    for pkg_name in cache.keys():
        pkg_match = r.match(pkg_name)
        if not pkg_match:
            continue
        pkg = cache[pkg_name]
        pkg_data = None
        if pkg.candidate:
            pkg_data = pkg.candidate
        elif pkg.installed:
            pkg_data = pkg.installed
        else:
            continue
        version = pkg_match.group(1)
        kernel_type = pkg_match.group(2)
        full_version = f"{version}{kernel_type}{pkg_match.group(3)}"
        used = 0
        if pkg.is_installed:
            pkg_version = pkg.installed.version
            if full_version == current_version:
                used = 1
        elif kernel_type == default_kernel_type and pkg.candidate and pkg.candidate.downloadable:
            # only offer to install same-type kernels
            pkg_version = pkg.candidate.version
        else:
            continue
        if pkg.is_auto_installed:
            installed = 2
        else:
            installed = int(pkg.is_installed)
        # filter duplicates (unsigned kernels where signed exists)
        if full_version in signed_kernels:
            continue
        signed_kernels.append(full_version)

        # provide a representation of the version which helps sorting the kernels
        version_id = KernelVersion(pkg_version).version_id

        if not pkg_data.origins[0].origin:
            origin = Origin.LOCAL
            if not kernel_type in local_kernels.keys():
                local_kernels[kernel_type] = []
            local_kernels[kernel_type].append(version_id[:4])
        elif pkg_data.origins[0].origin == "Ubuntu":
            origin = Origin.UBUNTU
        elif pkg_data.origins[0].origin == "Debian":
            origin = Origin.DEBIAN
        else:
            origin = Origin.OTHER

        archive = pkg_data.origins[0].archive

        # get support duration
        supported_tag = pkg_data.record.get("Supported")
        if not supported_tag and origin == Origin.UBUNTU and not "-proposed" in pkg_data.origins[0].archive:
            # Workaround for Ubuntu releasing kernels by copying straight from
            # -proposed and only adding the Supported tag shortly after.
            # To avoid user confusion in the time in-between we just assume
            # that all Ubuntu kernels in all pockets but -proposed are supported
            # and generate the supported tag based on the distro support duration
            distro = pkg_data.origins[0].archive.split("-")[0]
            if distro in release_dates:
                distro_lifetime = (release_dates[distro][1].year - release_dates[distro][0].year) * 12 +\
                                    release_dates[distro][1].month - release_dates[distro][0].month
                if distro_lifetime >= 12:
                    supported_tag = f"{distro_lifetime // 12}y"
                else:
                    supported_tag = f"{distro_lifetime}m"
        if supported_tag:
            if supported_tag.endswith("y"):
                # override support duration for HWE kernels in LTS releases,
                # these will be handled by the kernel window
                if "-hwe" in pkg_data.source_name:
                    support_duration = -1
                else:
                    support_duration = int(supported_tag[:-1]) * 12
            elif supported_tag.endswith("m"):
                support_duration = int(supported_tag[:-1])
            else:
                # unexpected support tag
                support_duration = 0
        else:
            # unsupported
            support_duration = 0

        resultString = f"KERNEL###{'.'.join(version_id)}###{version}###{pkg_version}###{installed}###{used}" \
                        f"###{origin}###{archive}###{support_duration}###{kernel_type}"
        print(resultString.encode("utf-8").decode("ascii", "xmlcharrefreplace"))

except:
    import traceback
    print("ERROR###ERROR###ERROR###ERROR")
    traceback.print_exc()
    sys.exit(1)

if USE_MAINLINE_KERNELS:
    try:
        mainline_kernel_versions = MainlineKernels(flavor=default_kernel_type).get_available_versions()
        for mainline_version in mainline_kernel_versions:
            # Filter already installed mainline kernels (or what we hope are
            # mainline builds based on the first 4 digit groups)
            # Note: This also filters multiple builds of the same version,
            # e.g. v4.17 and v4.17-keep
            version_id = KernelVersion(mainline_version).version_id
            display_version = f"{'.'.join((str(int(x)) for x in version_id[:3]))}-{version_id[3].strip('z')}"
            if default_kernel_type in local_kernels.keys() and \
            version_id in local_kernels[default_kernel_type]:
                continue
            resultString = f"KERNEL###{'.'.join(version_id)}###{display_version}###{mainline_version}" \
                        f"###0###0###3######0###{default_kernel_type}"
            print(resultString.encode("utf-8").decode('ascii', 'xmlcharrefreplace'))
    except:
        print("ERROR: List of available mainline kernels could not be retrieved")
