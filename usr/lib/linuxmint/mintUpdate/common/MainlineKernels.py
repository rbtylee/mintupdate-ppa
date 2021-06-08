import hashlib
import json
import os
import re
import subprocess
import tempfile
from html.parser import HTMLParser

import requests

from common import settings
from common.constants import ROOT_FUNCTIONS
from common.functions import configured_kernel_type


PPA_URL = "https://kernel.ubuntu.com/~kernel-ppa/mainline/"
CACHEFOLDER = "/var/cache/mintupdate/"

class MainlineKernelData:
    """ Holds mainline kernel type-specific data, `name` must be the folder name in the PPA """
    def __init__(self, name=None, title="", is_daily=False):
        self.title = _("Ubuntu mainline kernel")
        if title:
            self.title += f"{self.title} - {title}"
        if name:
            self.base_url = f"{PPA_URL}{name}/"
        else:
            name = "ppa"
            self.base_url = PPA_URL
        self.cachefile = f"{CACHEFOLDER}mainline-{name}"
        self.is_daily = is_daily
        self.name = name

    def versioned_url(self, version):
        """
        Returns a URI to the folder at `PPA_URL` corresponding to `version`.
        See `self.format_version()` for supported version formats.
        """
        if self.is_daily:
            return f"{self.base_url}{self.format_version(version)}/"
        else:
            return f"{self.base_url}v{self.format_version(version)}/"

    def changelog_url(self, version):
        """
        Returns a URI to the CHANGES file at `PPA_URL` corresponding to `version`.
        See `self.format_version()` for supported version formats.
        """
        return f"{self.versioned_url(version)}CHANGES"

    def format_version(self, version):
        """
        Formats a kernel version into the format required for `PPA_URL`.

        Examples:

        * Any of 5.5.0-050500rc2-generic, 5.5-rc2, 5.5.0-rc2, 050500rc2 will be returned as 5.5-rc2

        * Any of 5.5.0-050500-generic, 5.5, 5.5.0, 050500, 050500z will be returned as 5.5
        """
        # there's only one format for daily builds, don't change a thing
        if self.is_daily:
            return version
        # version numbers for versioned builds can be in several formats,
        # detect and process them
        if version.count("-") > 1:
            version = version.split("-")[1]
        if "-" in version:
            # 5.5-rc2 format
            version, suffix = version.split("-", 1)
        else:
            # everything else
            suffix_pos = version.find("rc")
            if suffix_pos > 0:
                suffix = f"{version[suffix_pos:]}"
                version = version[:suffix_pos]
            else:
                suffix = ""
        if suffix:
            suffix = f"-{suffix}"
        if not "." in version:
            # e.g. 050500
            version = f"{int(version[0:2])}.{int(version[2:4])}.{int(version[4:6])}"
        if version.endswith(".0"):
            # e.g. 5.5.0
            version = version[:-2]
        version = f"{version}{suffix}"
        return version


MAINLINE_KERNEL_DATA = {
    0: MainlineKernelData(),
    999: MainlineKernelData("daily", "daily build", True),
    # https://drm.pages.freedesktop.org/maintainer-tools/maintainer-drm-intel.html
    994: MainlineKernelData("drm-tip", "drm-tip build", True), # equals drm-intel-nightly
    996: MainlineKernelData("drm-next", "drm-next build", True),
    997: MainlineKernelData("drm-intel-next", "drm-intel-next build", True),
}

class KernelPPA_DailyIndexParser(HTMLParser):
    """ Returns the newest entry from a daily builds index """

    def __init__(self):
        super().__init__()
        self.new_row = False
        self.r = re.compile(r"^(\d{4}-\d{2}-\d{2})\/$")
        self.daily_build = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.new_row = True

    def handle_data(self, data):
        if self.new_row:
            match = self.r.match(data)
            if match:
                self.daily_build = data[:-1]

    def error(self, message):
        raise Exception(message)

class MainlineKernels:
    """
    Class for handling various tasks surrounding use of Ubuntu's kernel.org
    kernel builds.
    """

    class MainlineKernelsException(Exception):
        """ Base exception class to catch """

    class DownloadError(MainlineKernelsException):
        """ Generic exception class for download errors """

    class KernelUnavailable(MainlineKernelsException):
        """ Generic exception class for unavailable kernels """

    class ParserError(MainlineKernelsException):
        """ Generic exception class for download errors """

    def __init__(self, branch_id=0, cached=True, flavor=""):
        if not branch_id in MAINLINE_KERNEL_DATA:
            raise self.KernelUnavailable("Unknown kernel type")

        self.tmpfolder = os.path.join(tempfile.gettempdir(), "mintUpdate/")
        self.supported_cachefile = f"{CACHEFOLDER}mainline-support-status"
        self.supported_url = "https://www.kernel.org/"
        self.use_cache = cached
        self.include_rc = settings.get_boolean("mainline-include-rc")
        self.include_longterm = settings.get_boolean("mainline-include-longterm")
        self.supported_mainline_kernel_types = ["-generic", "-lowlatency"]
        if not flavor:
            self.configured_kernel_type = configured_kernel_type()
        else:
            self.configured_kernel_type = flavor
        self.base_data = MAINLINE_KERNEL_DATA[branch_id]

    def get_daily_build(self):
        """
        Returns the newest daily build
        """
        if self.configured_kernel_type not in self.supported_mainline_kernel_types:
            return []

        daily_build = self._load_cache(self.base_data.cachefile)
        if not daily_build:

            try:
                r = requests.get(self.base_data.base_url, timeout=5)
                if not r.ok:
                    raise self.DownloadError
            except:
                raise self.DownloadError(_("Failed to retrieve daily mainline builds list."))
            try:
                mainline_kernels = KernelPPA_DailyIndexParser()
                mainline_kernels.feed(r.text)
                mainline_kernels.close()
                daily_build = mainline_kernels.daily_build
            except:
                raise self.ParserError(_("Failed to retrieve daily mainline builds list."))
            self._write_cache(self.base_data.cachefile, daily_build)

        return daily_build

    def get_support_status(self):
        """
        Parses kernel.org front page and returns a dictionary containing
        series:support_status, where support_status is either End of Life or
        the string used on kernel.org (i.e. mainline, stable, longterm)
        """

        class KernelOrgParser(HTMLParser):
            """ Parses HTML into `self.supported_series` dictionary """

            def __init__(self):
                super().__init__()
                self.new_row = False
                self.r = re.compile(r"^(\d\.\d+)")
                self.supported_series = {}
                self.current_series = None
                self.column = 0
                self.type = None
                self.tag = None
                self.attrs = None

            def handle_starttag(self, tag, attrs):
                self.tag = tag
                self.attrs = attrs
                if tag == "tr" and attrs == [('align', 'left')]:
                    self.current_series = None
                    self.new_row = True
                    self.type = None
                    self.column = 0
                if self.new_row and tag == "td":
                    self.column += 1

            def handle_endtag(self, tag):
                self.tag = None

            def handle_data(self, data):
                if self.new_row and self.tag:
                    if self.column == 1 and self.tag == "td":
                        # Kernel support type, e.g. mainline, stable, longterm
                        self.type = data[:-1]
                    elif self.column == 2 and self.type:
                        if self.tag == "strong":
                            # Version number
                            match = self.r.match(data)
                            if match:
                                self.current_series = f"{match.group(1)}"
                                self.supported_series[self.current_series] = self.type
                        elif self.tag == "span" and \
                            next((True for x in self.attrs if "eolkernel" in x), False):
                            # # Remove still listed but end-of-life kernels:
                            # del self.supported_series[self.current_series]
                            self.supported_series[self.current_series] = "eol"
                    elif self.column > 2:
                        self.new_row = False

            def error(self, message):
                raise Exception(message)

        supported_series = self._load_cache(self.supported_cachefile)

        if not supported_series:
            try:
                r = requests.get(self.supported_url, timeout=5)
                if not r.ok:
                    raise self.DownloadError
            except:
                raise self.DownloadError(_("Failed to retrieve mainline kernel support status."))
            try:
                mainline_kernels = KernelOrgParser()
                mainline_kernels.feed(r.text)
                mainline_kernels.close()
                supported_series = mainline_kernels.supported_series
            except:
                raise self.ParserError(_("Failed to parse mainline kernel support status."))
            self._write_cache(self.supported_cachefile, supported_series)

        return supported_series

    def get_available_versions(self, filter_eol=True, filter_rc=True, filter_longterm=True):
        """
        Returns list of mainline kernel versions.

        Pass `filter_eol=false` to also include no longer supported series.

        Release candidates are only included if no release version exists in the
        series and `filter_rc=False` is passed or `self.include_rc`is `True`.

        Longterm support series are only included if `filter_longterm=False` is
        passed or `self.include_longterm` is `True`
        """

        if self.configured_kernel_type not in self.supported_mainline_kernel_types:
            return []

        class KernelPPA_IndexParser(HTMLParser):
            """ Parses HTML into `self.versions` list """

            def __init__(self):
                super().__init__()
                self.new_row = False
                self.r = re.compile(r"^v(\d)\.(\d+)(.*?)?\/$")
                self.versions = []

            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    self.new_row = True

            def handle_data(self, data):
                if self.new_row:
                    match = self.r.match(data)
                    if match:
                        self.versions.append(data[1:-1])
                        self.new_row = False

            def error(self, message):
                raise Exception(message)

        mainline_kernel_versions = self._load_cache(self.base_data.cachefile)

        if not mainline_kernel_versions:
            try:
                r = requests.get(self.base_data.base_url, timeout=5)
                if not r.ok:
                    raise self.DownloadError
            except:
                raise self.DownloadError(_("Failed to retrieve mainline kernel list."))
            try:
                mainline_kernels = KernelPPA_IndexParser()
                mainline_kernels.feed(r.text)
                mainline_kernels.close()
                mainline_kernel_versions = mainline_kernels.versions[::-1]
            except:
                raise self.ParserError(_("Failed to parse mainline kernel list."))
            self._write_cache(self.base_data.cachefile, mainline_kernel_versions)

        if filter_eol or filter_rc or filter_longterm:
            if filter_eol:
                try:
                    supported_series = self.get_support_status()
                except self.MainlineKernelsException as e:
                    print(e)
                    supported_series = {}

            filtered = []
            known_series = []
            r = re.compile(r"^(\d)\.(\d+)(-rc\d)?.*$")
            for kernel in mainline_kernel_versions:
                match = r.match(kernel)
                kernel_series = f"{match.group(1)}.{match.group(2)}"
                is_rc = match.group(3) is not None
                if not kernel_series in known_series and not is_rc:
                    known_series.append(kernel_series)
                # Filter conditition getting a bit complex:
                if (
                        not filter_eol or
                        (
                            # Supported kernels
                            kernel_series in supported_series and
                            (
                                # Longterm support kernels
                                not filter_longterm or
                                self.include_longterm or
                                supported_series[kernel_series] != "longterm"
                            )
                        )
                   ) and \
                   (
                        # Release candidates that have no released version in
                        # the series
                        not is_rc or
                        (
                            kernel_series not in known_series and
                            (not filter_rc or self.include_rc)
                        )
                   ):
                    filtered.append(kernel)
            return filtered

        return mainline_kernel_versions

    def get_filelist(self, version, get_size=True):
        """
        Returns a dictionary of the files making up the given kernel version.
        Dictionary keys are "filename" and "size", with size only getting
        populated if called with get_size=True, otherwise size is 0.
        """
        class KernelPPA_KernelParser(HTMLParser):
            """ Parses HTML into `self.files` list """

            def __init__(self, kernel_type:str, arch:str):
                super().__init__()
                self.new_row = False
                self.r = re.compile(r"^(.+?)\-(.+?)\-(\d\.\d+\.\d+\-\d+(?:rc\d)?)(\-.+?)?_(.+?)\_(.+?)\.deb$")
                self.arch = arch
                self.kernel_type = kernel_type
                self.files = []

            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    self.new_row = True

            def handle_data(self, data):
                if self.new_row:
                    match = self.r.match(data)
                    if match and match.group(1) == "linux" and \
                       (not match.group(4) or match.group(4) == self.kernel_type) and \
                       (match.group(6) == "all" or match.group(6) == self.arch):
                        self.files.append(data)
                        self.new_row = False

            def error(self, message):
                raise Exception(message)

        arch = os.uname().machine
        if arch == "x86_64":
            arch = "amd64"

        cachefile = f"{self.base_data.cachefile}-kernel"
        filelist = self._load_cache(cachefile)
        if filelist:
            if not filelist[0] == version:
                filelist = []
            elif filelist[1] == "FAILED":
                raise self.KernelUnavailable(_(f"Mainline kernel {version} is unavailable (failed to build)"))
        if not filelist:
            filelist = [version]
            session = requests.Session()
            try:
                r = session.get(self.base_data.versioned_url(version), timeout=5)
                if not r.ok:
                    session.close()
                    raise self.DownloadError
            except:
                try:
                    session.close()
                except:
                    pass
                raise self.DownloadError(_(f"Failed to retrieve mainline kernel {version} data"))
            if f"Build for {arch} failed" in r.text:
                filelist.append("FAILED")
                self._write_cache(cachefile, filelist)
                session.close()
                raise self.KernelUnavailable(_(f"Mainline kernel {version} is unavailable (failed to build)"))
            try:
                mainline_kernel = KernelPPA_KernelParser(kernel_type=self.configured_kernel_type, arch=arch)
                mainline_kernel.feed(r.text)
                mainline_kernel.close()
            except:
                session.close()
                raise self.ParserError(_(f"Failed to parse mainline kernel {version} data"))
            if not mainline_kernel.files or len(mainline_kernel.files) < 4:
                session.close()
                raise self.KernelUnavailable(_(f"Mainline kernel {version} is unavailable (failed to build)"))
            for filename in mainline_kernel.files:
                size = 0
                if get_size:
                    try:
                        r = session.get(f"{self.base_data.base_url}{filename}", stream=True, timeout=5)
                        length = r.headers.get("Content-Length")
                        if length:
                            size = int(length)
                    except:
                        pass
                filelist.append({"filename": filename, "size": size})
            session.close()
            self._write_cache(cachefile, filelist)
        return filelist[1:]

    def get_changelog(self, version):
        """
        Returns a string containing Ubuntu's CHANGES file containing the git
        commit log or on failure an empty string.
        """
        try:
            r = requests.get(self.base_data.changelog_url(version), timeout=5)
            if r.ok:
                r.encoding = None
                return r.text
        except:
            pass
        return ""

    def download_files(self, version, filelist):
        """
        Downloads `filelist` of packages for mainline kernel `version` into
        `self.tmpfolder`
        """
        import shutil

        if not os.path.exists(self.tmpfolder):
            os.umask(0)
            os.makedirs(self.tmpfolder)
        base_url = self.base_data.versioned_url(version)
        session = requests.Session()
        downloaded_files = []
        for filename in filelist:
            download_failed = False
            url = base_url + filename
            try:
                r = session.get(url, stream=True, timeout=5)
            except:
                download_failed = True
            if not download_failed and r.ok:
                try:
                    with open(self.tmpfolder + filename, "wb") as outfile:
                        r.raw.decode_content = True
                        shutil.copyfileobj(r.raw, outfile)
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
        return downloaded_files

    @staticmethod
    def verify_checksums(checksums, downloaded_files):
        checksums = checksums.split("Checksums-Sha256:", 1)
        if not len(checksums) == 2:
            return False
        checksums = checksums[1]
        for downloaded_file in downloaded_files:
            for line in checksums.split("\n"):
                line = line.split()
                if not len(line) == 2:
                    continue
                if os.path.basename(downloaded_file) == line[1]:
                    # calculate SHA256 hash for downloaded file
                    sha256_hash = hashlib.sha256()
                    with open(downloaded_file, "rb") as f:
                        while True:
                            data = f.read(65536)
                            if not data:
                                break
                            sha256_hash.update(data)
                    # compare to the reference hash
                    if sha256_hash.hexdigest() != line[0]:
                        return False
        # If we didn't fail up to here, all files must have been verified
        return True

    @staticmethod
    def install(debfiles, is_upgrade=False):
        """
        Installs all .deb files in `debfiles`.

        Tries to elevate with sudo if not root.
        """
        if not debfiles:
            return False
        cmd = [ROOT_FUNCTIONS,  "mainline"]
        if not os.getuid() == 0:
            cmd.insert(0, "sudo")
        if is_upgrade:
            cmd.append("upgrade")
        else:
            cmd.append("install")
        cmd.extend(debfiles)
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            return e.returncode
        return 0

    def _load_cache(self, cachefile):
        """
        Returns a json object loaded from `cachefile` or `None`.
        """
        try:
            if self.use_cache and os.path.exists(cachefile):
                with open(cachefile) as f:
                    return json.load(f)
        except:
            pass
        return None

    @staticmethod
    def _write_cache(cachefile, data):
        """
        Writes json object `data` into `cachefile` but only if run as root.
        """
        if not data or not os.getuid() == 0:
            return
        try:
            cachedir = os.path.dirname(cachefile)
            if not os.path.exists(cachedir):
                os.makedirs(cachedir)
            with open(cachefile, "w") as f:
                json.dump(data, f)
        except:
            pass
