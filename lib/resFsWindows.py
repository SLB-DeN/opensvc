import os

import rcStatus
from rcGlobalEnv import rcEnv
rcMounts = __import__('rcMounts'+rcEnv.sysname)
import resFs as Res
import rcExceptions as ex

def diskpartfile_name(self):
    return os.path.join(rcEnv.paths.pathvar, 'diskpart_' + self.svc.svcname)

def online_drive(self, driveindex):
    diskpart_file = diskpartfile_name(self) + '_online_disk_' + str(driveindex)
    with open(diskpart_file, 'w') as f:
        f.write("select disk=%s\n"%driveindex)
        f.write("online disk\n")
        f.write("exit\n")
    self.log.info("bring disk %s online"%driveindex)
    cmd = ['diskpart', '/s', diskpart_file]
    (ret, out, err) = self.vcall(cmd)
    if ret != 0:
        raise ex.excError("Failed to run command %s"% ' '.join(cmd) )

def offline_drive(self, driveindex):
    diskpart_file = diskpartfile_name(self) + '_offline_disk_' + str(driveindex)
    with open(diskpart_file, 'w') as f:
        f.write("select disk=%s\n"%driveindex)
        f.write("offline disk\n")
        f.write("exit\n")
    self.log.info("bring disk %s offline"%driveindex)
    cmd = ['diskpart', '/s', diskpart_file]
    (ret, out, err) = self.vcall(cmd)
    if ret != 0:
        raise ex.excError("Failed to run command %s"% ' '.join(cmd) )

def mount(self):
    v = self.get_volume()
    letter = self.mountpt_to_driverletter()
    ret = 0
    self.log.info("assign drive letter %s to volume %s"%(letter, self.device))
    v.DriveLetter = letter
    if not v.Automount:
        self.log.info("mount volume %s"%self.device)
        ret, = v.Mount()
    v = self.get_volume(refresh=True)
    return ret

def try_umount(self):
    v = self.get_volume()
    letter = self.mountpt_to_driverletter()
    self.log.info("unassign drive letter %s from volume %s"%(letter, self.device))
    v.DriveLetter = None
    v = self.get_volume(refresh=True)
    if v.DriveLetter is None:
        return 0
    return 1

class Mount(Res.Mount):
    """ define Windows mount/umount doAction """

    def on_add(self):
        if hasattr(self.svc, 'wmi'):
            return
        import wmi
        self.svc.wmi = wmi.WMI()

    def mountpt_to_driverletter(self):
        i = self.mount_point.index(':')
        return self.mount_point[:i+1]

    def get_volume(self, refresh=False):
        if not refresh and hasattr(self, "volume"):
            return getattr(self, "volume")
        l = self.svc.wmi.Win32_Volume()
        for v in l:
            print("DBG devid %s " % v.DeviceId)
            if v.DeviceId == self.device:
                # cache result
                self.volume = v
                return v
        raise ex.excError("Volume %s not found" % self.device)

    # a completer
    def match_mount(self, i, dev, mnt):
        return True

    # a completer
    def is_online(self, dev):
        return True

    def is_up(self):
        return rcMounts.Mounts(wmi=self.svc.wmi).has_mount(self.device, self.mount_point)

    def start(self):
        if self.is_online(self.device) is True:
            self.log.info("fs(%s %s) is already online"%(self.device, self.mount_point))
        if self.is_up() is True:
            self.log.info("fs(%s %s) is already mounted"%(self.device, self.mount_point))
            return 0
        ret = mount(self)
        if ret != 0:
            return 1
        self.can_rollback = True
        return 0

    def stop(self):
        if self.is_up() is False:
            self.log.info("fs(%s %s) is already umounted"%
                    (self.device, self.mount_point))
            return 0
        ret = try_umount(self)
        if ret != 0:
            raise ex.excError('failed to dismount %s'%self.mount_point)
        return 0

if __name__ == "__main__":
    for c in (Mount,) :
        help(c)
