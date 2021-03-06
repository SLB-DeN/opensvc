from rcGlobalEnv import rcEnv
import resources as Res
import os
import rcExceptions as ex
import resContainer

rcU = __import__("rcUtilities" + os.uname()[0])

class Xen(resContainer.Container):
    def __init__(self,
                 rid,
                 name,
                 guestos=None,
                 osvc_root_path=None,
                 **kwargs):
        resContainer.Container.__init__(self,
                                        rid=rid,
                                        name=name,
                                        type="container.xen",
                                        guestos=guestos,
                                        osvc_root_path=osvc_root_path,
                                        **kwargs)

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def list_conffiles(self):
        cf = os.path.join(os.sep, 'opt', 'opensvc', 'var', self.name+'.xml')
        if os.path.exists(cf):
            return [cf]
        return []

    def files_to_sync(self):
        return self.list_conffiles()

    def check_capabilities(self):
        cmd = ['virsh', 'capabilities']
        (ret, out, err) = self.call(cmd, errlog=False)
        if ret != 0:
            self.status_log("can not fetch capabilities")
            return False
        return True

    def ping(self):
        return rcU.check_ping(self.addr, timeout=1, count=1)

    def container_start(self):
        cf = os.path.join(os.sep, 'opt', 'opensvc', 'var', self.name+'.xml')
        if os.path.exists(cf):
            cmd = ['virsh', 'define', cf]
            (ret, buff, err) = self.vcall(cmd)
            if ret != 0:
                raise ex.excError
        cmd = ['virsh', 'start', self.name]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_stop(self):
        cmd = ['virsh', 'shutdown', self.name]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_forcestop(self):
        cmd = ['virsh', 'destroy', self.name]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def is_up_on(self, nodename):
        return self.is_up(nodename)

    def is_up(self, nodename=None):
        cmd = ['virsh', 'dominfo', self.name]
        if nodename is not None:
            cmd = rcEnv.rsh.split() + [nodename] + cmd
        (ret, out, err) = self.call(cmd, errlog=False)
        if ret != 0:
            return False
        if "running" in out.split() or "idle" in out.split() :
            return True
        return False

    def get_container_info(self):
        cmd = ['virsh', 'dominfo', self.name]
        (ret, out, err) = self.call(cmd, errlog=False, cache=True)
        self.info = {'vcpus': '0', 'vmem': '0'}
        if ret != 0:
            return self.info
        for line in out.split('\n'):
            if "CPU(s):" in line: self.info['vcpus'] = line.split(':')[1].strip()
            if "Max memory" in line: self.info['vmem'] = line.split(':')[1].strip()
            if "Autostart:" in line: self.info['autostart'] = line.split(':')[1].strip()
        return self.info

    def check_manual_boot(self):
        self.get_container_info()
        if self.info['autostart'] == 'disable' :
                return True
        else:
                return False
