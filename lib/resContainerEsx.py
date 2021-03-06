import resources as Res
import os
import rcExceptions as ex
import resContainer
from rcUtilities import which

rcU = __import__("rcUtilities" + os.uname()[0])

class Esx(resContainer.Container):
    def __init__(self,
                 rid,
                 name,
                 guestos=None,
                 osvc_root_path=None,
                 **kwargs):
        resContainer.Container.__init__(self,
                                        rid=rid,
                                        name=name,
                                        guestos=guestos,
                                        type="container.esx",
                                        osvc_root_path=osvc_root_path,
                                        **kwargs)
        self.vmx = None

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def vmcmd(self, l, verbose=False):
        self.find_vmx()
        if verbose:
            ret, out, err = self.vcall(['vmware-cmd', self.vmx] + l)
        else:
            ret, out, err = self.call(['vmware-cmd', self.vmx] + l)
        return ret, out, err

    def check_capabilities(self):
        return which('vmware-cmd')

    def ping(self):
        return rcU.check_ping(self.addr, timeout=1, count=1)

    def find_vmx(self):
        if self.vmx is not None:
            return self.vmx
        cmd = ['vmware-cmd', '-l']
        ret, out, err = self.call(cmd)
        if ret != 0:
            raise ex.excError
        l = out.split()
        pattern = '/'+self.name+'.vmx'
        for vmx in l:
            if pattern in vmx:
                self.vmx = vmx
        if self.vmx is None:
            self.log.error("vm %s not found on this node"%self.name)
            raise ex.excError
        return self.vmx

    def _migrate(self):
        print("TODO")
        pass

    def container_start(self):
        cmd = ['start']
        ret, buff, err = self.vmcmd(cmd, verbose=True)
        if ret != 0:
            raise ex.excError

    def container_stop(self):
        cmd = ['stop', 'soft']
        ret, buff, err = self.vmcmd(cmd, verbose=True)
        if ret != 0:
            raise ex.excError

    def container_forcestop(self):
        cmd = ['stop', 'hard']
        ret, buff, err = self.vmcmd(cmd, verbose=True)
        if ret != 0:
            raise ex.excError

    def is_up(self):
        try:
            (ret, out, err) = self.vmcmd(['getstate'])
        except:
            return False
        if ret != 0:
            return False
        l = out.split()
        if len(l) != 3:
            return False
        if l[-1] == 'on':
            return True
        return False

    def getconfig(self, key):
        cmd = ['getconfig', key]
        ret, out, err = self.vmcmd(cmd)
        if ret != 0:
            return None
        l = out.split()
        if len(l) != 3:
            return None
        return l[-1]

    def get_container_info(self):
        self.info = {'vcpus': '0', 'vmem': '0'}
        try:
            self.info['vcpus'] = self.getconfig('numvcpus')
        except:
            self.info['vcpus'] = '0'
        try:
            self.info['vmem'] = self.getconfig('memsize')
        except:
            self.info['vmem'] = '0'
        return self.info

    def check_manual_boot(self):
        """ ESX will handle the vm startup itself """
        return True
