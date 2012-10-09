import rcDevTree
import glob
import os
import re
import math
from subprocess import *
from rcUtilities import which
from rcGlobalEnv import rcEnv
di = __import__("rcDiskInfo"+rcEnv.sysname)
_di = di.diskInfo()

class DevTree(rcDevTree.DevTree):
    dev_h = {}

    def get_size(self, devpath):
        size = 0
        with open(devpath+'/size', 'r') as f:
            try:
                size = int(f.read().strip()) * 512 / 1024 / 1024
            except:
                pass
        return size

    def get_dm(self):
        if hasattr(self, 'dm_h'):
            return self.dm_h
        self.dm_h = {}
        if not os.path.exists("/dev/mapper"):
            return self.dm_h
        devpaths = glob.glob("/dev/mapper/*")
        devpaths.remove('/dev/mapper/control')
        for devpath in devpaths:
            s = os.stat(devpath)
            minor = os.minor(s.st_rdev)
            self.dm_h[devpath.replace("/dev/mapper/", "")] = "dm-%d"%minor

        # reverse hash
        self._dm_h = {}
        for mapname, devname in self.dm_h.items():
            self._dm_h[devname] = mapname

        return self.dm_h

    def get_map_wwid(self, map):
        if not which("multipath"):
            return None
        if not hasattr(self, 'multipath_l'):
            self.multipath_l = []
            cmd = ['multipath', '-l']
            p = Popen(cmd, stdout=PIPE, stderr=PIPE)
            out, err = p.communicate()
            if p.returncode != 0:
                return None
            self.multipath_l = out.split('\n')
        for line in self.multipath_l:
            if not line.startswith(map):
                continue
            try:
                wwid = line[line.index('(')+2:line.index(')')]
            except ValueError:
                wwid = line.split()[0]
            return wwid
        return None

    def get_wwid(self):
        if hasattr(self, 'wwid_h'):
            return self.wwid_h
        self.wwid_h = {}
        self.wwid_h.update(self.get_wwid_native())
        self.wwid_h.update(self.get_mp_powerpath())
        return self.wwid_h

    def get_wwid_native(self):
        if not which("multipath"):
            return self.wwid_h
        cmd = ['multipath', '-l']
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return self.wwid_h
        for line in out.split('\n'):
            if 'dm-' not in line:
                continue
            devname = line[line.index('dm-'):].split()[0]
            try:
                wwid = line[line.index('(')+2:line.index(')')]
            except ValueError:
                wwid = line.split()[0]
            self.wwid_h[devname] = wwid
        return self.wwid_h

    def get_mp(self):
        if hasattr(self, 'mp_h'):
            return self.mp_h
        self.mp_h = {}
        self.mp_h.update(self.get_mp_native())
        self.mp_h.update(self.get_mp_powerpath())
        return self.mp_h

    def get_mp_powerpath(self):
        self.powerpath = {}
        if not which("powermt"):
            return {}
        cmd = ['powermt', 'display', 'dev=all']
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return {}
        lines = out.split('\n')
        if len(lines) < 1:
            return {}
        dev = None
        paths = []
        mp_h = {}
        for line in lines:
            if len(line) == 0:
                # new mpath
                # - store previous
                # - reset path counter
                if dev is not None:
                    if len(paths) > 0:
                        did = _di.disk_id(paths[0])
                    mp_h[name] = did
                    self.powerpath[name] = paths
                    dev = None
                    paths = []
            if 'Pseudo name' in line:
                l = line.split('=')
                if len(l) != 2:
                    continue
                name = l[1]
                dev = "/dev/"+name
            else:
                l = line.split()
                if len(l) < 3:
                    continue
                if l[2].startswith("sd"):
                    paths.append("/dev/"+l[2])
        return mp_h

    def get_mp_native(self):
        if not which('dmsetup'):
            return {}
        cmd = ['dmsetup', 'ls', '--target', 'multipath']
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return {}
        mp_h = {}
        for line in out.split('\n'):
            l = line.split()
            if len(l) == 0:
                continue
            mapname = l[0]
            major = l[1].strip('(,')
            minor = l[2].strip(' )')
            mp_h['dm-'+minor] = mapname
        return mp_h

    def get_md(self):
        if hasattr(self, 'md_h'):
            return self.md_h
        fpath = "/proc/mdstat"
        self.md_h = {}
        try:
            with open(fpath, 'r') as f:
                buff = f.read()
        except:
            return self.md_h
        for line in buff.split('\n'):
            if line.startswith("Personalities"):
                continue
            if len(line) == 0 or line[0] == " ":
                continue
            l = line.split()
            if len(l) < 4:
                continue
            self.md_h[l[0]] = l[3]
        return self.md_h

    def load_dm_dev_t(self):
        table = {}
        if not which('dmsetup'):
            return
        cmd = ['dmsetup', 'ls']
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return
        for line in out.split('\n'):
            l = line.split()
            if len(l) == 0:
                continue
            mapname = l[0]
            major = l[1].strip('(,')
            minor = l[2].strip(' )')
            dev_t = ':'.join((major, minor))
            self.dev_h[dev_t] = mapname

    def load_dm(self):
        table = {}
        self.load_dm_dev_t()
        if not which('dmsetup'):
            return
        cmd = ['dmsetup', 'table']
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return
        for line in out.split('\n'):
            l = line.split()
            if len(l) < 5:
                continue
            mapname = l[0].strip(':')
            size = int(math.ceil(1.*int(l[2])*512/1024/1024))
            maptype = l[3]

            if maptype == "multipath" and size in [0, 2, 3, 30, 45]:
                continue

            for w in l[4:]:
                if ':' not in w:
                    continue
                if mapname not in table:
                    table[mapname] = {"devs": [], "size": 0, "type": "linear"}
                table[mapname]["devs"].append(w)
                table[mapname]["size"] += size
                table[mapname]["type"] = maptype
        for mapname in table:
            d = self.add_dev(mapname, table[mapname]["size"])
            d.set_devtype(table[mapname]["type"])

            d.set_devpath('/dev/mapper/'+mapname)
            s = mapname.replace('--', ':').replace('-', '/').replace(':','-')
            if "/" in s:
                d.set_devpath('/dev/'+s)
            wwid = self.get_map_wwid(mapname)
            if wwid is not None:
                d.set_alias(wwid)
            for dev in table[mapname]["devs"]:
                d.add_parent(self.dev_h[dev])
                parentdev = self.get_dev(self.dev_h[dev])
                parentdev.add_child(mapname)


    def get_lv_linear(self):
        if hasattr(self, 'lv_linear'):
            return self.lv_linear
        self.lv_linear = {}
        if not which('dmsetup'):
            return self.lv_linear
        cmd = ['dmsetup', 'table', '--target', 'linear']
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return self.lv_linear
        for line in out.split('\n'):
            l = line.split(':')
            if len(l) < 2:
                continue
            mapname = l[0]
            line = line[line.index(':')+1:]
            l = line.split()
            if len(l) < 3:
                continue
            length = int(l[1])*512/1024/1024
            devt = l[3]
            if mapname in self.lv_linear:
                self.lv_linear[mapname].append((devt, length))
            else:
                self.lv_linear[mapname] = [(devt, length)]
        return self.lv_linear

    def is_cdrom(self, devname):
        p = '/sys/block/%s/device/media'%devname
        if not os.path.exists(p):
            return False
        with open(p, 'r') as f:
            buff = f.read()
        if buff.strip() == "cdrom":
            return True
        return False

    def dev_type(self, devname):
        t = "linear"
        md_h = self.get_md()
        mp_h = self.get_mp()
        if devname in md_h:
            return md_h[devname]
        if devname in mp_h:
            return "multipath"
        return t

    def add_drbd_relations(self):
        if not which("drbdadm") or not os.path.exists('/proc/drbd'):
            return
        cmd = ["drbdadm", "dump-xml"]
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return
        from xml.etree import ElementTree as etree
        tree = etree.fromstring(out)
        for res in tree.getiterator('resource'):
            for host in res.findall('host'):
                if host.attrib['name'] != rcEnv.nodename:
                    continue
                edisk = host.find('disk')
                edev = host.find('device')
                if edisk is None or edev is None:
                    continue
                devname = 'drbd'+edev.attrib['minor']
                parentpath = edisk.text
                d = self.get_dev_by_devpath(parentpath)
                if d is None:
                    continue
                d.add_child(devname)
                c = self.get_dev(devname)
                c.add_parent(d.devname)

    def load_dev(self, devname, devpath):
        if self.is_cdrom(devname):
            return

        mp_h = self.get_mp()
        wwid_h = self.get_wwid()
        size = self.get_size(devpath)

        # exclude 0-sized md, Symmetrix gatekeeper and vcmdb
        if devname in self.mp_h and size in [0, 2, 30, 45]:
            return

        devtype = self.dev_type(devname)
        d = self.add_dev(devname, size, devtype)

        if d is None:
            return

        if 'cciss' in devname:
            d.set_devpath('/dev/'+devname.replace('!', '/'))
        else:
            d.set_devpath('/dev/'+devname)

        # store devt
        with open("%s/dev"%devpath, 'r') as f:
            devt = f.read().strip()
            self.dev_h[devt] = devname

        # add holders
        holderpaths = glob.glob("%s/holders/*"%devpath)
        holdernames = map(lambda x: os.path.basename(x), holderpaths)
        for holdername, holderpath in zip(holdernames, holderpaths):
            size = self.get_size(holderpath)
            devtype = self.dev_type(holdername)
            d.add_child(holdername, size, devtype)

        # add lv aliases
        self.get_dm()
        if devname in self._dm_h:
            d.set_alias(self._dm_h[devname])
            d.set_devpath('/dev/mapper/'+self._dm_h[devname])
            s = self._dm_h[devname].replace('--', ':').replace('-', '/').replace(':','-')
            d.set_devpath('/dev/'+s)

        # add slaves
        slavepaths = glob.glob("%s/slaves/*"%devpath)
        slavenames = map(lambda x: os.path.basename(x), slavepaths)
        for slavename, slavepath in zip(slavenames, slavepaths):
            size = self.get_size(slavepath)
            devtype = self.dev_type(slavename)
            d.add_parent(slavename, size, devtype)

        if devname in mp_h:
            d.set_alias(wwid_h[devname])

        return d

    def get_dev_t(self, dev):
        major, minor = self._get_dev_t(dev)
        return ":".join((str(major), str(minor)))

    def _get_dev_t(self, dev):
        try:
            s = os.stat(dev)
            minor = os.minor(s.st_rdev)
            major = os.major(s.st_rdev)
        except:
            return 0, 0
        return major, minor

    def load_fdisk(self):
        self.get_wwid()
        os.environ["LANG"] = "C"
        p = Popen(["fdisk", "-l"], stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return
        for line in out.split("\n"):
            if line.startswith('/dev/dm-'):
                continue
            elif line.startswith("Disk "):
                # disk
                devpath = line.split()[1].strip(':')
                if devpath.startswith('/dev/dm-'):
                    continue
                size = int(line.split()[-2]) / 1024 / 1024
                if size in [2, 3, 30, 45]:
                    continue
                devname = devpath.replace('/dev/','').replace("/","!")
                devtype = self.dev_type(devname)
                dev_t = self.get_dev_t(devpath)
                self.dev_h[dev_t] = devname
                d = self.add_dev(devname, size, devtype)
                if d is None:
                    continue
                d.set_devpath(devpath)
                if devname.startswith('emc') and devname in self.wwid_h:
                    d.set_alias(self.wwid_h[devname])
                    for path in self.powerpath[devname]:
                        p = self.add_dev(path.replace('/dev/',''), size, "linear")
                        p.set_devpath(path)
                        p.add_child(devname)
                        d.add_parent(path.replace('/dev/',''))
            elif line.startswith('/dev/'):
                # partition
                line = line.replace('*', '')
                partpath = line.split()[0]
                partsize = int(line.split()[3].replace('+','')) * 512 / 1024 / 1024
                partname = partpath.replace('/dev/','').replace("/","!")
                dev_t = self.get_dev_t(partpath)
                self.dev_h[dev_t] = partname
                p = self.add_dev(partname, partsize, "linear")
                if p is None:
                    continue
                p.set_devpath(partpath)
                d.add_child(partname)
                p.add_parent(devname)

    def load_sysfs(self):
        devpaths = glob.glob("/sys/block/*")
        devnames = map(lambda x: os.path.basename(x), devpaths)
        for devname, devpath in zip(devnames, devpaths):
            d = self.load_dev(devname, devpath)

            if d is None:
                continue

            # add parts
            partpaths = glob.glob("%s/%s*"%(devpath, devname))
            partnames = map(lambda x: os.path.basename(x), partpaths)
            for partname, partpath in zip(partnames, partpaths):
                p = self.load_dev(partname, partpath)
                if p is None:
                    continue
                d.add_child(partname)
                p.add_parent(devname)
 
    def tune_lv_relations(self):
        dm_h = self.get_dm()
        for lv, segments in self.get_lv_linear().items():
            for devt, length in segments:
                child = dm_h[lv]
                parent = self.dev_h[devt]
                r = self.get_relation(parent, child)
                if r is not None:
                    r.set_used(length)

    def load(self):
        if len(glob.glob("/sys/block/*/slaves")) == 0:
            self.load_fdisk()
            self.load_dm()
        else:
            self.load_sysfs()
            self.tune_lv_relations()

        self.add_drbd_relations()

    def blacklist(self, devname):
        bl = [r'^loop[0-9]*.*', r'^ram[0-9]*.*', r'^scd[0-9]*', r'^sr[0-9]*']
        for b in bl:
            if re.match(b, devname):
                return True
        return False

if __name__ == "__main__":
    tree = DevTree()
    tree.load()
    #print tree
    tree.print_tree_bottom_up()
    #print map(lambda x: x.alias, tree.get_top_devs())
