import os
import re
import time

from rcGlobalEnv import *
from rcUtilities import call, which, clear_cache
import rcStatus
import resDiskLoop as Res
import rcExceptions as ex
from rcLoopSunOS import file_to_loop
from lock import cmlock

class Disk(Res.Disk):
    def is_up(self):
        """Returns True if the loop group is present and activated
        """
        self.loop = file_to_loop(self.loopFile)
        if len(self.loop) == 0:
            return False
        return True

    def start(self):
        lockfile = os.path.join(rcEnv.paths.pathlock, "disk.loop")
        if self.is_up():
            self.log.info("%s is already up" % self.label)
            return
        try:
            with cmlock(timeout=30, delay=1, lockfile=lockfile):
                cmd = ['lofiadm', '-a', self.loopFile]
                ret, out, err = self.vcall(cmd)
        except Exception as exc:
            raise ex.excError(str(exc))
        if ret != 0:
            raise ex.excError
        self.loop = file_to_loop(self.loopFile)
        if len(self.loop) == 0:
            raise ex.excError("loop device did not appear or disappeared")
        time.sleep(1)
        self.log.info("%s now loops to %s" % (self.loop, self.loopFile))
        self.can_rollback = True

    def stop(self):
        if not self.is_up():
            self.log.info("%s is already down" % self.label)
            return 0
        cmd = ['lofiadm', '-d', self.loop]
        ret, out, err = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def resource_handling_file(self):
        path = os.path.dirname(self.loopFile)
        return self.svc.resource_handling_dir(path)

    def _status(self, verbose=False):
        r = self.resource_handling_file()
        if self.is_provisioned() and not os.path.exists(self.loopFile):
            if r is None or (r and r.status() in (rcStatus.UP, rcStatus.STDBY_UP)):
                self.status_log("%s does not exist" % self.loopFile)
        if self.is_up():
            return rcStatus.UP
        else:
            return rcStatus.DOWN

    def __init__(self, rid, loopFile, **kwargs):
        Res.Disk.__init__(self, rid, loopFile, **kwargs)

    def exposed_devs(self):
        self.loop = file_to_loop(self.loopFile)
        return set(self.loop)
