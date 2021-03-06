import resources as Res
import rcExceptions as ex
from rcUtilities import qcall
import resContainer
from rcGlobalEnv import rcEnv
import os

utilities = __import__('rcUtilities'+rcEnv.sysname)

class Vbox(resContainer.Container):
    def __init__(self,
                 rid,
                 name,
                 guestos=None,
                 headless=None,
                 osvc_root_path=None,
                 **kwargs):
        resContainer.Container.__init__(self,
                                        rid=rid,
                                        name=name,
                                        type="container.vbox",
                                        guestos=guestos,
                                        osvc_root_path=osvc_root_path,
                                        **kwargs)
        #self.sshbin = '/usr/local/bin/ssh'
        self.vminfo = None
        self.headless = headless

    def __str__(self):
        return "%s name=%s" % (Res.Resource.__str__(self), self.name)

    def get_vminfo(self):
        if self.vminfo is not None:
            return self.vminfo
        cmd = ['VBoxManage', 'showvminfo', '--machinereadable', self.name]
        (ret, out, err) = self.call(cmd)
        if ret != 0:
            return None
        h = {}
        for line in out.split('\n'):
            l = line.split('=')
            if len(l) != 2:
                continue
            key = l[0].strip('"')
            val = l[1].strip('"')
            h[key] = val
        self.vminfo = h
        return self.vminfo

    def files_to_sync(self):
        a = []
        vminfo = self.get_vminfo()
        if vminfo is None:
            return []
        a.append(vminfo['CfgFile'])
        a.append(vminfo['SnapFldr'])
        a.append(vminfo['LogFldr'])
        return a

    def check_capabilities(self):
        cmd = ['VBoxManage', '-v']
        (ret, out, err) = self.call(cmd)
        if ret != 0:
            return False
        return True

    def state(self, nodename=None):
        cmd = ['VBoxManage', 'list', 'runningvms']
        if nodename is not None:
            cmd = rcEnv.rsh.split() + [nodename] + cmd
        (ret, out, err) = self.call(cmd)
        if ret != 0:
            return None
        for line in out.split('\n'):
            l = line.split('"')
            if len(l) < 2:
                continue
            if l[1] == self.name:
                return 'on'
        return 'off'

    def ping(self):
        return utilities.check_ping(self.addr)

    def container_action(self, action, add=[]):
        cmd = ['VBoxManage', action, self.name] + add
        (ret, buff, err) = self.vcall(cmd)
        if ret != 0:
            raise ex.excError

    def container_start(self):
        state = self.state()
        if state == 'None':
            raise ex.excError
        elif state == 'off':
            if self.headless:
                self.container_action('startvm', ['--type', 'headless'])
            else:
                self.container_action('startvm')
        elif state == 'on':
            self.log.info("container is already up")

    def container_forcestop(self):
        self.container_action('controlvm', ['poweroff'])

    def container_stop(self):
        state = self.state()
        if state == 'None':
            raise ex.excError
        elif state == 'off':
            self.log.info("container is already down")
        if state == 'on' :
            self.container_action('controlvm', ['acpipowerbutton'])
            try:
                self.log.info("wait for container shutdown")
                self.wait_for_fn(self.is_shutdown, self.stop_timeout, 2)
            except ex.excError:
                self.container_forcestop()

    def check_manual_boot(self):
        return True

    def is_shutdown(self):
        state = self.state()
        if state == 'off':
            return True
        return False

    def is_down(self):
        if self.state() == 'off':
            return True
        return False

    def is_up_on(self, nodename):
        return self.is_up(nodename)

    def is_up(self, nodename=None):
        if self.state(nodename) == 'on':
            return True
        return False

