import provisioning
from rcGlobalEnv import rcEnv
import os
import socket
import rcExceptions as ex
from rcUtilities import which, lazy

class Prov(provisioning.Prov):
    def __init__(self, r):
        provisioning.Prov.__init__(self, r)

        self.name = r.name
        self.rootpath = os.path.join(os.sep, 'var', 'hpsrp', self.name)
        self.need_start = []

    @lazy
    def ip(self):
        ip = self.r.svc.oget(self.r.rid, "ip")
        return self.lookup(ip)

    @lazy
    def prm_cores(self):
        return self.r.svc.oget(self.r.rid, "prm_cores")

    def lookup(self, ip):
        if ip is None:
            raise ex.excError("the ip provisioning keyword is not set")

        try:
            int(ip[0])
            # already in cidr form
            return
        except:
            pass

        try:
            a = socket.getaddrinfo(ip, None)
            if len(a) == 0:
                raise Exception
            ip = a[0][-1][0]
            return ip
        except:
            raise ex.excError("could not resolve %s to an ip address"%ip)

    def validate(self):
        # False triggers provisioner, True skip provisioner
        if not which('srp'):
            self.r.log.error("this node is not srp capable")
            return True

        if self.check_srp():
            self.r.log.error("container is already created")
            return True

        return False

    def check_srp(self):
        try:
            self.r.get_status()
        except:
            return False
        return True

    def cleanup(self):
        rs = self.r.svc.get_resources('fs')
        rs.sort(key=lambda x: x.mount_point, reverse=True)
        for r in rs:
            if r.mount_point == self.rootpath:
                continue
            if not r.mount_point.startswith(self.rootpath):
                continue
            r.stop()
            self.need_start.append(r)
            os.unlink(r.mount_point)
            p = r.mount_point
            while True:
                p = os.path.realpath(os.path.join(p, '..'))
                if p == self.rootpath:
                    break
                try:
                    self.r.log.info("unlink %s"%p)
                    os.unlink(p)
                except:
                    break

    def restart_fs(self):
        for r in self.need_start:
            r.start()

    def add_srp(self):
        self.cleanup()
        cmd = ['srp', '-batch',
               '-a', self.name,
               '-t', 'system',
               '-s', 'admin,cmpt,init,prm,network',
               'ip_address='+self.ip, 'assign_ip=no',
               'autostart=no',
               'delete_files_ok=no',
               'root_password=""',
               'prm_group_type=PSET',
               'prm_cores='+str(self.prm_cores)]
        ret, out, err = self.r.vcall(cmd)
        if ret != 0:
            raise ex.excError()
        self.restart_fs()

    def provisioner(self):
        self.add_srp()
        self.r.start()
        self.r.log.info("provisioned")
        return True
