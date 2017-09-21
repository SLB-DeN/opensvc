import resources as Res
import os
import rcExceptions as ex
import rcStatus
from rcGlobalEnv import rcEnv
from rcUtilities import is_string, lazy
import pwd
import grp
import stat

class FsDir(Res.Resource):
    """Define a mount resource
    """

    def __init__(self,
                 rid=None,
                 path=None,
                 user=None,
                 group=None,
                 perm=None,
                 **kwargs):
        Res.Resource.__init__(self,
                              rid=rid,
                              type="fs",
                              **kwargs)
        self.type = "fs.dir"
        self.path = path
        self.mount_point = path # for fs ordering
        self.user = user
        self.group = group
        self.perm = perm
        self.label = "dir " + path

    def start(self):
        self.create()

    def get_gid(self):
        if is_string(self.group):
            info = grp.getgrnam(self.group)
            self.gid = info[2]
        else:
            self.gid = int(self.group)

    def get_uid(self):
        if is_string(self.user):
            info = pwd.getpwnam(self.user)
            self.uid = info[2]
        else:
            self.uid = int(self.user)

    def create(self):
        if not os.path.exists(self.path):
            self.log.info("create directory %s" % (self.path))
            os.makedirs(self.path)
        if not self.check_uid():
            self.log.info("set %s user to %s" % (self.path, str(self.user)))
            os.chown(self.path, self.uid, -1)
        if not self.check_gid():
            self.log.info("set %s group to %s" % (self.path, str(self.group)))
            os.chown(self.path, -1, self.gid)
        if not self.check_perm():
            self.log.info("set %s perm to %s" % (self.path, str(self.perm)))
            os.chmod(self.path, int(str(self.perm), 8))

    def check_uid(self):
        if self.user is None:
            return True
        if not os.path.exists(self.path):
            return True
        self.get_uid()
        uid = os.stat(self.path).st_uid
        if uid != self.uid:
            self.status_log('uid should be %s but is %s'%(str(self.uid), str(uid)))
            return False
        return True

    def check_gid(self):
        if self.group is None:
            return True
        if not os.path.exists(self.path):
            return True
        self.get_gid()
        gid = os.stat(self.path).st_gid
        if gid != self.gid:
            self.status_log('gid should be %s but is %s'%(str(self.gid), str(gid)))
            return False
        return True

    def check_perm(self):
        if self.perm is None:
            return True
        if not os.path.exists(self.path):
            return True
        perm = oct(stat.S_IMODE(os.stat(self.path).st_mode))
        perm = str(perm).lstrip("0o").lstrip("0")
        if perm != str(self.perm):
            self.status_log('perm should be %s but is %s'%(str(self.perm), perm))
            return False
        return True

    @lazy
    def parent_dir(self):
        return os.path.realpath(os.path.join(self.path, ".."))

    def _status(self, verbose=False):
        if not os.path.exists(self.path):
            self.log.debug("dir %s does not exist" % self.path)
            return rcStatus.DOWN
        self.check_uid()
        self.check_gid()
        self.check_perm()
        parent = self.svc.resource_handling_dir(self.parent_dir)
        if self.status_logs_count(["warn", "error"]) > 0:
            return rcStatus.WARN
        elif parent is None:
            return rcStatus.NA
        elif parent.status() in (rcStatus.UP, rcStatus.STDBY_UP):
            return rcStatus.UP
        else:
            return rcStatus.DOWN

    def __str__(self):
        return "%s path=%s user=%s group=%s perm=%s" % (Res.Resource.__str__(self),\
                self.path, str(self.user), str(self.group), str(self.perm))

    def __lt__(self, other):
        """
        Order so that deepest mountpoint can be umount first
        """
        return self.mount_point < other.mount_point




