import time
import resIpSunOS as Res
import rcExceptions as ex
from subprocess import *
from rcGlobalEnv import rcEnv
rcIfconfig = __import__('rcIfconfig'+rcEnv.sysname)

class Ip(Res.Ip):
    def __init__(self,
                 rid=None,
                 ipdev=None,
                 ipname=None,
                 zone=None,
                 mask=None,
                 gateway=None,
                 **kwargs):
        Res.Ip.__init__(self,
                        rid=rid,
                        ipdev=ipdev,
                        ipname=ipname,
                        mask=mask,
                        gateway=gateway,
                        **kwargs)
        self.zone = zone
        self.tags.add(zone)
        self.tags.add('zone')

    def startip_cmd(self):
        cmd=['ifconfig', self.stacked_dev, 'plumb', self.addr, \
             'netmask', '+', 'broadcast', '+', 'up' , 'zone' , self.zone ]
        return self.vcall(cmd)

    def stopip_cmd(self):
        cmd=['ifconfig', self.stacked_dev, 'unplumb']
        return self.vcall(cmd)

    def allow_start(self):
        retry = 1
        interval = 0
        import time
        ok = False
        if 'noalias' not in self.tags:
            for i in range(retry):
                ifconfig = rcIfconfig.ifconfig()
                intf = ifconfig.interface(self.ipdev)
                if intf is not None and intf.flag_up:
                    ok = True
                    break
                time.sleep(interval)
            if not ok:
                self.log.error("Interface %s is not up. Cannot stack over it." % self.ipdev)
                raise ex.IpDevDown(self.ipdev)
        if self.is_up() is True:
            self.log.info("%s is already up on %s" % (self.addr, self.ipdev))
            raise ex.IpAlreadyUp(self.addr)
        if not hasattr(self, 'abort_start_done') and 'nonrouted' not in self.tags and self.check_ping():
            self.log.error("%s is already up on another host" % (self.addr))
            raise ex.IpConflict(self.addr)
        return

if __name__ == "__main__":
    for c in (Ip,) :
        help(c)

