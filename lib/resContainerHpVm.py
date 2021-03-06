import rcStatus
import resources as Res
import time
import os
import rcExceptions as ex
from rcUtilities import qcall
from rcGlobalEnv import rcEnv
import resContainer
u = __import__('rcUtilitiesHP-UX')
import resDiskHpVm

class HpVm(resContainer.Container):
    def __init__(self,
                 rid,
                 name,
                 guestos="HP-UX",
                 osvc_root_path=None,
                 **kwargs):
        resContainer.Container.__init__(self,
                                        rid=rid,
                                        name=name,
                                        type="container.hpvm",
                                        guestos=guestos,
                                        osvc_root_path=osvc_root_path,
                                        **kwargs)
        self.vg = resDiskHpVm.Disk(
            rid = 'vmdg#'+self.rid,
            name = 'vmdg_'+self.name,
            container_name = self.name
        )

    def on_add(self):
        self.vg.svc = self.svc

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def files_to_sync(self):
        import glob
        a = self.vg.files_to_sync()
        guest = os.path.join(os.sep, 'var', 'opt', 'hpvm', 'guests', self.name)
        uuid = os.path.realpath(guest)
        if os.path.exists(guest):
            a.append(guest)
        if os.path.exists(uuid):
            a.append(uuid)
        return a

    def ping(self):
        return u.check_ping(self.addr, timeout=1, count=1)

    def container_start(self):
        cmd = ['/opt/hpvm/bin/hpvmstart', '-P', self.name]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_stop(self):
        cmd = ['/opt/hpvm/bin/hpvmstop', '-g', '-F', '-P', self.name]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_forcestop(self):
        cmd = ['/opt/hpvm/bin/hpvmstop', '-F', '-P', self.name]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def check_manual_boot(self):
        cmd = ['/opt/hpvm/bin/hpvmstatus', '-M', '-P', self.name]
        (ret, out, err) = self.call(cmd, cache=True)
        if ret != 0:
            return False
        if out.split(":")[11] == "Manual":
            return True
        self.log.info("Auto boot should be turned off")
        return False

    def get_container_info(self):
        cmd = ['/opt/hpvm/bin/hpvmstatus', '-M', '-P', self.name]
        (ret, out, err) = self.call(cmd, cache=True)
        self.info = {'vcpus': '0', 'vmem': '0'}
        if ret != 0:
            return self.info
        self.info['vcpus'] = out.split(':')[19].split(';')[0]
        self.info['vmem'] = out.split(':')[20].split(';')[0]
        if 'GB' in self.info['vmem']:
            self.info['vmem'] = str(1024*1024*int(self.info['vmem'].replace('GB','')))
        return self.info

    def is_up_on(self, nodename):
        return self.is_up(nodename)

    def is_up(self, nodename=None):
        cmd = ['/opt/hpvm/bin/hpvmstatus', '-M', '-P', self.name]
        if nodename is not None:
            cmd = rcEnv.rsh.split() + [nodename] + cmd
        (ret, out, err) = self.call(cmd)
        if ret != 0:
            return False
        if out.split(":")[10] == "On":
            return True
        return False

    def check_capabilities(self):
        if os.path.exists('/opt/hpvm/bin/hpvmstatus'):
            return True
        return False

    def _migrate(self):
        cmd = ['hpvmmigrate', '-o', '-P', self.name, '-h', self.svc.options.to]
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def sub_disks(self):
        return self.vg.sub_disks()

    def sub_devs(self):
        return self.vg.sub_devs()

    def presync(self):
        return self.vg.presync()

    def postsync(self):
        return self.vg.postsync()
