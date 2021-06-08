class KernelVersion:

    def __init__(self, version):
        field_length = 3
        self.version = version
        self.version_id = []
        _version_id = version.replace("-", ".").split(".")
        # Check if mainline rc kernel to ensure proper sorting vs mainline release kernels
        suffix = next((x for x in _version_id if "rc" in x), None)
        if suffix:
            suffix = f"rc{suffix.split('rc', 1)[1]}"
        else:
            suffix = "z"
        # Copy numeric parts from version_id to self.version_id and fill up to field_length
        for element in _version_id:
            e = element.replace(suffix, "")
            if e and e[0].isnumeric():
                self.version_id.append("0" * (field_length - len(e)) + e)
        # Installed kernels always have len(self.version_id) >= 4 at this point,
        # create missing parts for not installed mainline kernels:
        while len(self.version_id) < 3:
            self.version_id.append("0" * field_length)
        if len(self.version_id) == 3:
            _version_id = self.version_id.copy() # not needed but gets me around a pylint error
            self.version_id.append(f"{''.join((x[:field_length - 2].lstrip('0') + x[field_length - 2:] for x in _version_id))}{suffix}")
        elif len(self.version_id[3]) == 6:
            # installed release mainline kernel, add suffix for sorting
            self.version_id[3] += suffix
        self.series = tuple(self.version_id[:3])
        self.shortseries = tuple(self.version_id[:2])
