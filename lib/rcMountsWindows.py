import rcMounts

class Mounts(rcMounts.Mounts):

    def match_mount(self, i, dev, mnt):
        """Given a line of 'mount' output, returns True if (dev, mnt) matches
        this line. Returns False otherwise.
        """
        if i.mnt != mnt:
            return False
        if i.dev == dev:
            return True
        return False

    def parse_mounts(self, wmi=None):
        if wmi is None:
            import wmi
            wmi = wmi.WMI()
        mounts = []
        for volume in wmi.Win32_Volume():
            dev = volume.DeviceID
            mnt = volume.Name
            if mnt is None:
                mnt = ""
            type = volume.FileSystem
            mnt_opt = "NULL"    # quoi mettre d autre...
            m = rcMounts.Mount(dev, mnt, type, mnt_opt)
            mounts.append(m)
        return mounts

if __name__ == "__main__" :
    #help(Mounts)
    for m in Mounts():
        print(m)
