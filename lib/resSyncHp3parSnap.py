import os

from rcGlobalEnv import rcEnv
import rcExceptions as ex
import rcStatus
from rcUtilities import justcall
import resSync
import datetime
import rcHp3par as rc

class syncHp3parSnap(resSync.Sync):
    def __init__(self,
                 rid=None,
                 array=None,
                 vv_names=[],
                 **kwargs):
        resSync.Sync.__init__(self,
                              rid=rid,
                              type="sync.hp3parsnap",
                              **kwargs)
        self.array = array
        self.vv_names = vv_names
        self.label = "hp3parsnap %s" % ", ".join(self.vv_names)
        if len(self.label) > 50:
            self.label = self.label[:47] + "..."
        self.default_schedule = "@0"

    def __str__(self):
        return self.label

    def on_add(self):
        try:
            arrays = rc.Hp3pars(objects=[self.array], log=self.log, node=self.svc.node)
        except Exception as e:
            raise ex.excInitError(str(e))
        if len(arrays.arrays) == 1:
            self.array_obj = arrays.arrays[0]
        else:
            self.array_obj = None

        if self.array_obj is None:
            self.log.error("no 3par array object")
            return
        self.array_obj.path = self.svc.path

    def can_sync(self, target=None, s=None):
        data = self.showvv()
        if len(data) < len(self.vv_names):
            return False
        last = self.lastsync_s_to_datetime(data[0]['CreationTime'])
        if self.skip_sync(datetime.datetime.utcnow()-last):
            return False
        try:
            self.check_requires("sync_update")
        except ex.excError:
            return False
        return True

    def updatevv(self):
        self.array_obj.updatevv(vvnames=self.vv_names, log=self.log)

    @resSync.notify
    def sync_update(self):
        self.updatevv()
        self.array_obj.clear_caches()

    def lastsync_s_to_datetime(self, s):
        out, err, ret = justcall(["date", "--utc", "--date=%s" % s, '+%Y-%m-%d %H:%M:%S'])
        d = datetime.datetime.strptime(out.strip(), "%Y-%m-%d %H:%M:%S")
        return d

    def showvv(self):
        return self.array_obj.showvv(vvprov="snp", vvnames=self.vv_names, cols=["Name", "CreationTime"])

    def _status(self, verbose=False):
        if self.array_obj is None:
            self.status_log("array %s is not accessible" % self.array)
            return rcStatus.WARN
        if not self.array_obj.has_virtualcopy():
            self.status_log("array %s has no virtual copy license" % self.array)
            return rcStatus.WARN

        try:
            data = self.showvv()
        except ex.excError as e:
            self.status_log(str(e))
            return rcStatus.WARN

        r = None
        if len(data) < len(self.vv_names):
            missing = set(self.vv_names) - set([d["Name"] for d in data])
            for m in missing:
                self.status_log("missing vv: %s" % m)
            r = rcStatus.WARN

        elapsed = datetime.datetime.utcnow() - datetime.timedelta(seconds=self.sync_max_delay)
        for vv in data:
            if self.lastsync_s_to_datetime(vv['CreationTime']) < elapsed:
                self.status_log("vv %s last sync too old (%s)"%(vv['Name'], vv['CreationTime']))
                r = rcStatus.WARN

        if r is not None:
            return r

        return rcStatus.UP

