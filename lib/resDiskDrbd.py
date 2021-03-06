import os
import resources as Res
import rcStatus
import rcExceptions as ex
from rcUtilities import which, justcall
from rcGlobalEnv import rcEnv

class Drbd(Res.Resource):
    """ Drbd device resource

        The tricky part is that drbd devices can be used as PV
        and LV can be used as drbd base devices. Beware of the
        the ordering deduced from rids and subsets.

        Start 'ups' and promotes the drbd devices to primary.
        Stop 'downs' the drbd devices.
    """

    def __init__(self,
                 rid=None,
                 res=None,
                 **kwargs):
        Res.Resource.__init__(self,
                              rid,
                              "disk.drbd",
                              **kwargs)
        self.res = res
        self.label = "drbd "+res
        self.drbdadm = None
        self.rollback_even_if_standby = True

    def __str__(self):
        return "%s resource=%s" % (Res.Resource.__str__(self),\
                                 self.res)

    def files_to_sync(self):
        cf = os.path.join(os.sep, 'etc', 'drbd.d', self.res+'.res')
        if os.path.exists(cf):
            return [cf]
        self.log.error("%s does not exist", cf)
        return []

    def drbdadm_cmd(self, cmd):
        if self.drbdadm is None:
            if which('drbdadm'):
                self.drbdadm = 'drbdadm'
            else:
                raise ex.excError("drbdadm command not found")
        return [self.drbdadm] + cmd.split() + [self.res]

    def exposed_devs(self):
        devps = set()

        ret, out, err = self.call(self.drbdadm_cmd('dump-xml'))
        if ret != 0:
            raise ex.excError

        from xml.etree.ElementTree import XML, fromstring
        tree = fromstring(out)

        for res in tree.getiterator('resource'):
            if res.attrib['name'] != self.res:
                continue
            for host in res.getiterator('host'):
                if host.attrib['name'] != rcEnv.nodename:
                    continue
                d = host.find('device')
                if d is not None:
                    devps |= set([d.text])
                    continue
                for volume in res.getiterator('volume'):
                    d = volume.find('device')
                    if d is None:
                        continue
                    devps |= set([d.text])
        return devps

    def sub_devs(self):
        devps = set()

        ret, out, err = self.call(self.drbdadm_cmd('dump-xml'))
        if ret != 0:
            raise ex.excError

        from xml.etree.ElementTree import XML, fromstring
        tree = fromstring(out)

        for res in tree.getiterator('resource'):
            if res.attrib['name'] != self.res:
                continue
            for host in res.getiterator('host'):
                if host.attrib['name'] != rcEnv.nodename:
                    continue
                d = host.find('disk')
                if d is None:
                    d = host.find('volume/disk')
                if d is None:
                    continue
                devps |= set([d.text])

        return devps

    def drbdadm_down(self):
        ret, out, err = self.vcall(self.drbdadm_cmd('down'))
        if ret != 0:
            raise ex.excError

    def drbdadm_up(self):
        ret, out, err = self.vcall(self.drbdadm_cmd('up'))
        if ret != 0:
            raise ex.excError
        self.wait_for_kwown_dstate()
        self.can_rollback_connection = True
        self.can_rollback = True

    def get_cstate(self):
        self.prereq()
        out, err, ret = justcall(self.drbdadm_cmd('cstate'))
        if ret != 0:
            if "Device minor not allocated" in err or ret == 10:
                return "Unattached"
            else:
                raise ex.excError
        return out.split("\n")[0].strip()

    def prereq(self):
        if not os.path.exists("/proc/drbd"):
            ret, out, err = self.vcall(['modprobe', 'drbd'])
            if ret != 0: raise ex.excError

    def start_connection(self):
        cstate = self.get_cstate()
        if cstate == "Connected":
            self.log.info("drbd resource %s is already connected", self.res)
        elif cstate == "Connecting":
            self.log.info("drbd resource %s is already connecting", self.res)
        elif cstate == "StandAlone":
            self.drbdadm_down()
            self.drbdadm_up()
        elif cstate == "WFConnection":
            self.log.info("drbd resource %s peer node is not listening", self.res)
            pass
        else:
            self.drbdadm_up()

    def get_role(self):
        out, err, ret = justcall(self.drbdadm_cmd('role'))
        if ret != 0:
            raise ex.excError(err)
        out = out.strip()
        if out in ("Primary", "Secondary"):
            # drbd9
            return out
        try:
            loc, rem = out.split("\n")[0].split('/')
        except (IndexError, ValueError, AttributeError):
            raise ex.excError(out)
        return loc

    def start_role(self, role):
        cur_role = self.get_role()
        if cur_role != role:
            ret, out, err = self.vcall(self.drbdadm_cmd(role.lower()))
            if ret != 0:
                raise ex.excError
            self.can_rollback_role = True
            self.can_rollback = True
        else:
            self.log.info("drbd resource %s is already %s", self.res, role)

    def startstandby(self):
        self.start_connection()
        role = self.get_role()
        if role == "Primary":
            return
        self.start_role('Secondary')

    def stopstandby(self):
        self.go_secondary()

    def go_secondary(self):
        self.start_connection()
        role = self.get_role()
        if role == "Secondary":
            return
        self.start_role('Secondary')

    def start(self):
        self.start_connection()
        self.start_role('Primary')

    def stop(self):
        if self.standby:
            self.stopstandby()
        else:
            self.drbdadm_down()

    def shutdown(self):
        self.drbdadm_down()

    def rollback(self):
        if not self.can_rollback:
            return
        if self.standby:
            if not self.can_rollback_role:
                return
            self.go_secondary()
        else:
            if not self.can_rollback_connection:
                return
            self.drbdadm_down()

    def get_dstate(self):
        ret, out, err = self.call(self.drbdadm_cmd('dstate'))
        if ret != 0:
            raise ex.excError
        return out.splitlines()

    def wait_for_kwown_dstate(self):
        def check():
            for dstate in self.get_dstate():
                if dstate == "Diskless/DUnknown":
                    return False
            return True
        self.wait_for_fn(check, 5, 1, errmsg="waited too long for a known remote dstate")

    def dstate_uptodate(self, dstates):
        for dstate in dstates:
            if dstate != "UpToDate/UpToDate":
                return False
        return True

    def _status(self, verbose=False):
        try:
            role = self.get_role()
        except Exception as e:
            self.status_log(str(e))
            return rcStatus.DOWN
        self.status_log(str(role), "info")
        try:
            dstates = self.get_dstate()
        except ex.excError:
            self.status_log("drbdadm dstate %s failed"%self.res)
            return rcStatus.WARN
        if self.dstate_uptodate(dstates):
            pass
        else:
            status = None
            for idx, dstate in enumerate(dstates):
                if dstate == "Diskless/DUnknown":
                    self.status_log("unexpected drbd resource %s/%d state: %s"%(self.res, idx, dstate))
                    status = rcStatus.DOWN
                elif dstate == "Unconfigured":
                    status = rcStatus.DOWN
                else:
                    self.status_log("unexpected drbd resource %s/%d state: %s"%(self.res, idx, dstate))
            if status is not None:
                return status
        if role == "Primary":
            return rcStatus.UP
        elif role == "Secondary" and self.standby:
            return rcStatus.STDBY_UP
        else:
            return rcStatus.WARN

if __name__ == "__main__":
    help(Drbd)
    v = Drbd(res='test')
    print(v)

