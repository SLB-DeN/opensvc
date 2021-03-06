import os
import subprocess
import time

import resources as Res
import rcExceptions as ex
import rcStatus
from rcGlobalEnv import rcEnv
from rcUtilities import which, mimport, lazy

class Mount(Res.Resource):
    """Define a mount resource
    """

    def __init__(self,
                 rid=None,
                 mount_point=None,
                 device=None,
                 fs_type=None,
                 mount_options=None,
                 stat_timeout=5,
                 snap_size=None,
                 **kwargs):
        Res.Resource.__init__(self,
                              rid=rid,
                              type="fs",
                              **kwargs)
        self.mount_point = mount_point
        self._device = device
        self.fs_type = fs_type
        self.stat_timeout = stat_timeout
        self.mount_options = mount_options
        self.snap_size = snap_size
        self.fsck_h = {}
        self.netfs = ['nfs', 'nfs4', 'cifs', 'smbfs', '9pfs', 'gpfs', 'afs', 'ncpfs']

    @lazy
    def testfile(self):
        if not self.mount_point:
            return
        return os.path.join(self.mount_point, '.opensvc')

    def set_fsck_h(self):
        """
        Placeholder
        """
        pass

    @lazy
    def device(self):
        if self._device is not None:
            if self.fs_type == "lofs" and not self._device.startswith(os.sep):
                l = self._device.split("/")
                vol = self.svc.get_volume(l[0])
                if vol.mount_point is not None:
                    l[0] = vol.mount_point
                    return "/".join(l)
            return self._device
        # lazy ref support, like {<rid>.exposed_devs[<n>]}
        return self.conf_get("dev")

    @lazy
    def label(self): # pylint: disable=method-hidden
        if self.device is None:
            label = self.svc._get(self.rid+".dev", evaluate=False)
        else:
            label = self.device
        if self.mount_point is not None:
            label += "@" + self.mount_point
        if self.fs_type not in ("tmpfs", "shm", "shmfs", "none", None):
            label = self.fs_type + " " + label
        return label

    def _info(self):
        data = [
          ["dev", self.device],
          ["mnt", self.mount_point],
          ["mnt_opt", self.mount_options if self.mount_options else ""],
        ]
        return data

    def start(self):
        self.validate_dev()
        self.promote_rw()
        self.create_mntpt()

    def validate_dev(self):
        if self.fs_type in ["zfs", "advfs"] + self.netfs:
            return
        if self.fs_type in ["bind", "lofs"] or "bind" in self.mount_options:
            return
        if self.device in ("tmpfs", "shm", "shmfs", "none"):
            # pseudo fs have no dev
            return
        if not self.device:
            raise ex.excError("device keyword not set or evaluates to None")
        if self.device.startswith("UUID=") or self.device.startswith("LABEL="):
            return
        if not os.path.exists(self.device):
            raise ex.excError("device does not exist %s" % self.device)

    def create_mntpt(self):
        if self.fs_type in ["zfs", "advfs"]:
            return
        if os.path.exists(self.mount_point):
            return
        try:
            os.makedirs(self.mount_point)
            self.log.info("create missing mountpoint %s" % self.mount_point)
        except:
            self.log.warning("failed to create missing mountpoint %s" % self.mount_point)

    def fsck(self):
        if self.fs_type in ("", "tmpfs", "shm", "shmfs", "none") or os.path.isdir(self.device):
            # bind mounts are in this case
            return
        self.set_fsck_h()
        if self.fs_type not in self.fsck_h:
            self.log.debug("no fsck method for %s"%self.fs_type)
            return
        bin = self.fsck_h[self.fs_type]['bin']
        if which(bin) is None:
            self.log.warning("%s not found. bypass."%self.fs_type)
            return
        if 'reportcmd' in self.fsck_h[self.fs_type]:
            cmd = self.fsck_h[self.fs_type]['reportcmd']
            (ret, out, err) = self.vcall(cmd, err_to_info=True)
            if ret not in self.fsck_h[self.fs_type]['reportclean']:
                return
        cmd = self.fsck_h[self.fs_type]['cmd']
        (ret, out, err) = self.vcall(cmd)
        if 'allowed_ret' in self.fsck_h[self.fs_type]:
            allowed_ret = self.fsck_h[self.fs_type]['allowed_ret']
        else:
            allowed_ret = [0]
        if ret not in allowed_ret:
            raise ex.excError

    def need_check_writable(self):
        if 'ro' in self.mount_options.split(','):
            return False
        if self.fs_type in self.netfs + ["tmpfs"]:
            return False
        return True

    def can_check_writable(self):
        """ orverload in child classes to check os-specific conditions
            when a write test might hang (solaris lockfs, linux multipath
            with queueing on and no active path)
        """
        return True

    def check_stat(self):
        if which("stat") is None:
            return True

        if self.device is None:
            return True

        proc = subprocess.Popen(['stat', self.device],
                                stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE)
        for retry in range(self.stat_timeout*10, 0, -1):
            if proc.poll() is None:
                time.sleep(0.1)
            else:
                return True
        try:
            proc.kill()
        except OSError:
            pass
        return False

    def check_writable(self):
        if self.testfile is None:
            return True
        if not self.can_check_writable():
            return False

        try:
            f = open(self.testfile, 'w')
            f.write(' ')
            f.close()
        except IOError as e:
            if e.errno == 28:
                self.log.error('No space left on device. Invalidate writable test.')
                return True
            return False
        except:
            return False

        return True

    def is_up(self):
        """
        Placeholder
        """
        return

    def _status(self, verbose=False):
        if self.is_up():
            if not self.check_stat():
                self.status_log("fs is not responding to stat")
                return rcStatus.WARN
            if self.need_check_writable() and not self.check_writable():
                self.status_log("fs is not writable")
                return rcStatus.WARN
            return rcStatus.UP
        else:
            return rcStatus.DOWN

    def sub_devs(self):
        pseudofs = [
          'lofs',
          'none',
          'proc',
          'sysfs',
        ]
        if self.fs_type in pseudofs + self.netfs:
            return set()
        if self.fs_type == "zfs":
            from rcZfs import zpool_devs
            return set(zpool_devs(self.device.split("/")[0], self.svc.node))
        for res in self.svc.get_resources():
            if hasattr(res, "is_child_dev") and res.is_child_dev(self.device):
                # don't account fs device if the parent resource is driven by the service
                return set()
        return set([self.device])

    def __str__(self):
        return "%s mnt=%s dev=%s fs_type=%s mount_options=%s" % (Res.Resource.__str__(self),\
                self.mount_point, self.device, self.fs_type, self.mount_options)

    def __lt__(self, other):
        """
        Order so that deepest mountpoint can be umount first.
        If no ordering constraint, honor the rid order.
        """
        try:
            smnt = os.path.dirname(self.mount_point)
            omnt = os.path.dirname(other.mount_point)
        except AttributeError:
            return self.rid < other.rid
        return (smnt, self.rid) < (omnt, other.rid)

    @lazy
    def prov(self):
        try:
            mod = mimport("prov", "fs", self.fs_type, fallback=True)
        except ImportError:
            return
        if not hasattr(mod, "Prov"):
            raise ex.excError("missing Prov class in module %s" % str(mod))
        return getattr(mod, "Prov")(self)


if __name__ == "__main__":
    for c in (Mount,) :
        help(c)
    print("""   m=Mount("/mnt1","/dev/sda1","ext3","rw")   """)
    m=Mount("/mnt1","/dev/sda1","ext3","rw")
    print("show m", m)


