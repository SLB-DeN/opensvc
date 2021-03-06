import resScsiReservSg
import rcStatus
from rcUtilitiesLinux import dev_to_paths, dev_is_ro

class ScsiReserv(resScsiReservSg.ScsiReserv):


    def mangle_devs(self, devs):
        return dict((dev, dev_to_paths(dev)) for dev in devs)


    def _status(self, verbose=False):
        ret = resScsiReservSg.ScsiReserv._status(self, verbose=verbose)
        if ret != rcStatus.UP:
            return ret
        self.get_devs()
        for dev, paths in self.devs.items():
            for path in paths:
                if dev_is_ro(path):
                    self.status_log("resv held on ro dev %s in %s" % (path, dev), "info")
                    return rcStatus.NA
        return ret
