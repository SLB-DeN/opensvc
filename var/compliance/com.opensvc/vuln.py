#!/usr/bin/env /opt/opensvc/bin/python
""" 
OSVC_COMP_VULN_11_117=\
[{"pkgname": "kernel", "minver": "2.6.18-238.19.1.el5", "firstver": "2.6.18-238"},\
 {"pkgname": "kernel-xen", "minver": "2.6.18-238.19.1.el5"}]
"""

import os
import sys
import json
import pwd
import sys
import re
import tempfile
from subprocess import *
from distutils.version import LooseVersion as V
from utilities import which


sys.path.append(os.path.dirname(__file__))

from comp import *

def repl(matchobj):
    return '.0'+matchobj.group(0)[1:]

class LiveKernVulnerable(Exception):
    pass

class CompVuln(object):
    def __init__(self, prefix='OSVC_COMP_VULN_', uri=None):
        self.uri = uri
        self.prefix = prefix.upper()
        self.highest_avail_version = "0"
        self.fix_list = []
        self.need_pushpkg = False
        self.sysname, self.nodename, x, x, self.machine = os.uname()

        if 'OSVC_COMP_VULN_STRICT' in os.environ and \
           os.environ['OSVC_COMP_VULN_STRICT'] == "true":
            self.strict = True
        else:
            self.strict = False

        if 'OSVC_COMP_VULN_PKG_TYPE' in os.environ and \
           os.environ['OSVC_COMP_VULN_PKG_TYPE'] == "bundle":
            self.pkg_type = 'bundle'
        else:
            self.pkg_type = 'product'

        self.packages = []
        for k in [key for key in os.environ if key.startswith(self.prefix)]:
            try:
                o = json.loads(self.subst(os.environ[k]))
            except ValueError:
                print >>sys.stderr, 'failed to concatenate', os.environ[k], 'to rules list'
                continue
            if type(o) != list:
                print >>sys.stderr, 'failed to concatenate', os.environ[k], 'to rules list'
                continue
            for i, d in enumerate(o):
                o[i]["rule"] = k.replace("OSVC_COMP_", "")
            self.packages += o

        if len(self.packages) == 0:
            raise NotApplicable()

        if self.sysname not in ['Linux', 'HP-UX', 'AIX', 'SunOS']:
            print >>sys.stderr, 'module not supported on', self.sysname
            raise NotApplicable()

        if 'OSVC_COMP_NODES_OS_VENDOR' not in os.environ:
            print >>sys.stderr, "OS_VENDOR is not set. Check your asset"
            raise NotApplicable()

        vendor = os.environ['OSVC_COMP_NODES_OS_VENDOR']
        if vendor in ['Debian', 'Ubuntu']:
            self.get_installed_packages = self.deb_get_installed_packages
            self.fix_pkg = self.apt_fix_pkg
            self.fixable_pkg = self.apt_fixable_pkg
            self.fix_all = None
        elif vendor in ['CentOS', 'Redhat', 'Red Hat'] or \
             (vendor == 'Oracle' and self.sysname == 'Linux'):
            self.get_installed_packages = self.rpm_get_installed_packages
            self.fix_pkg = self.yum_fix_pkg
            self.fixable_pkg = self.yum_fixable_pkg
            self.fix_all = None
        elif vendor in ['SuSE']:
            self.get_installed_packages = self.rpm_get_installed_packages
            self.fix_pkg = self.zyp_fix_pkg
            self.fixable_pkg = self.zyp_fixable_pkg
            self.fix_all = None
        elif vendor in ['HP']:
            if self.uri is None:
                print >>sys.stderr, "URI is not set"
                raise NotApplicable()
            self.get_installed_packages = self.hp_get_installed_packages
            self.fix_pkg = self.hp_fix_pkg
            self.fixable_pkg = self.hp_fixable_pkg
            self.fix_all = self.hp_fix_all
        elif vendor in ['IBM']:
            self.get_installed_packages = self.aix_get_installed_packages
            self.fix_pkg = self.aix_fix_pkg
            self.fixable_pkg = self.aix_fixable_pkg
            self.fix_all = None
        elif vendor in ['Oracle']:
            self.get_installed_packages = self.sol_get_installed_packages
            self.fix_pkg = self.sol_fix_pkg
            self.fixable_pkg = self.sol_fixable_pkg
            self.fix_all = None
        else:
            print >>sys.stderr, vendor, "not supported"
            raise NotApplicable()

        self.installed_packages = self.get_installed_packages()

    def subst(self, v):
        if type(v) == list:
            l = []
            for _v in v:
                l.append(self.subst(_v))
            return l
        if type(v) != str and type(v) != unicode:
            return v

        p = re.compile('%%ENV:\w+%%')
        for m in p.findall(v):
            s = m.strip("%").replace('ENV:', '')
            if s in os.environ:
                _v = os.environ[s]
            elif 'OSVC_COMP_'+s in os.environ:
                _v = os.environ['OSVC_COMP_'+s]
            else:
                print >>sys.stderr, s, 'is not an env variable'
                raise NotApplicable()
            v = v.replace(m, _v)
        return v.strip()

    def get_free(self, c):
        if not os.path.exists(c):
            return 0
        cmd = ["df", "-k", c]
        p = Popen(cmd, stdout=PIPE, stderr=None)
        out, err = p.communicate()
        for line in out.split():
            if "%" in line:
                l = out.split()
                for i, w in enumerate(l):
                    if '%' in w:
                        break
                try:
                    f = int(l[i-1])
                    return f
                except:
                    return 0
        return 0

    def get_temp_dir(self):
        if hasattr(self, "tmpd"):
            return self.tmpd
        candidates = ["/tmp", "/var/tmp", "/root"]
        free = {}
        for c in candidates:
            free[self.get_free(c)] = c
        max = sorted(free.keys())[-1]
        self.tmpd = free[max]
        print "selected %s as temp dir (%d KB free)" % (self.tmpd, max)
        return self.tmpd

    def download(self, pkg_name):
        import urllib
        import tempfile
        f = tempfile.NamedTemporaryFile(dir=self.get_temp_dir())
        dname = f.name
        f.close()
        try:
            os.makedirs(dname)
        except:
            pass
        fname = os.path.join(dname, "file")
        try:
            fname, headers = urllib.urlretrieve(pkg_name, fname)
        except IOError:
            import traceback
            e = sys.exc_info()
            try:
                os.unlink(fname)
                os.unlink(dname)
            except:
                pass
            raise Exception("download failed: %s" % str(e[1]))
        if 'invalid file' in headers.values():
            try:
                os.unlink(fname)
                os.unlink(dname)
            except:
                pass
            raise Exception("invalid file")
        with open(fname, 'r') as f:
            content = f.read(500)
        if content.startswith('<') and '404' in content and 'Not Found' in content:
            try:
                os.unlink(fname)
                os.unlink(dname)
            except:
                pass
            raise Exception("not found")
        import tarfile
        os.chdir(dname)
        try:
            tar = tarfile.open(fname)
        except:
            print "not a tarball"
            return fname
        try:
            tar.extractall()
        except:
            try:
                os.unlink(fname)
                os.unlink(dname)
            except:
                pass
            # must be a pkg
            return dname
        tar.close()
        os.unlink(fname)
        return dname

    def get_os_ver(self):
        cmd = ['uname', '-v']
        p = Popen(cmd, stdout=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            return 0
        lines = out.split('\n')
        if len(lines) == 0:
            return 0
        try:
            osver = float(lines[0])
        except:
            osver = 0
        return osver

    def sol_fix_pkg(self, pkg):
        r = self.check_pkg(pkg)
        if r == RET_OK:
            return RET_NA

        if 'repo' not in pkg or len(pkg['repo']) == 0:
            print >>sys.stderr, "no repo specified in the rule"
            return RET_NA

        pkg_url = pkg['repo']+"/"+pkg['pkgname']
        print "download", pkg_url
        try:
            dname = self.download(pkg_url)
        except Exception as e:
            print >>sys.stderr, e
            return RET_ERR

        if pkg["pkgname"] in self.installed_packages:
            os.chdir("/")
            yes = os.path.dirname(__file__) + "/yes"
            cmd = '%s | pkgrm %s' % (yes, pkg['pkgname'])
            print(cmd)
            r = os.system(cmd)
            if r != 0:
                return RET_ERR

        if os.path.isfile(dname):
            d = dname
        else:
            d = "."
            os.chdir(dname)

        if self.get_os_ver() < 10:
            opts = ''
        else:
            opts = '-G'

        if 'resp' in pkg and len(pkg['resp']) > 0:
            f = tempfile.NamedTemporaryFile(dir=self.get_temp_dir())
            resp = f.name
            f.close()
            with open(resp, "w") as f:
                f.write(pkg['resp'])
        else:
            resp = "/dev/null"
        yes = os.path.dirname(__file__) + "/yes"
        cmd = '%s | pkgadd -r %s %s -d %s all' % (yes, resp, opts, d)
        print(cmd)
        r = os.system(cmd)
        os.chdir("/")

        if os.path.isdir(dname):
            import shutil
            shutil.rmtree(dname)

        if r != 0:
            return RET_ERR
        return RET_OK

    def sol_fixable_pkg(self, pkg):
        return 0

    def sol_fix_all(self):
        return RET_NA

    def sol_get_installed_packages(self):
        p = Popen(['pkginfo', '-l'], stdout=PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            print >>sys.stderr, 'can not fetch installed packages list'
            return {}
        return self.sol_parse_pkginfo(out)

    def sol_parse_pkginfo(self, out):
        l = {}
        for line in out.split('\n'):
            v = line.split(':')
            if len(v) != 2:
                continue
            f = v[0].strip()
            if f == "PKGINST":
                pkgname = v[1].strip()
            elif f == "ARCH":
                pkgarch = v[1].strip()
            elif f == "VERSION":
                pkgvers = v[1].strip()
                if pkgname in l:
                    l[pkgname] += [(pkgvers, pkgarch)]
                else:
                    l[pkgname] = [(pkgvers, pkgarch)]
        return l

    def aix_fix_pkg(self, pkg):
        cmd = ['nimclient', '-o', 'cust',
               '-a', 'lpp_source=%s'%self.uri,
               '-a', 'installp_flags=agQY',
               '-a', 'filesets=%s'%pkg['pkgname']]
        s = " ".join(cmd)
        print s
        r = os.system(s)
        if r != 0:
            return RET_ERR
        return RET_OK

    def aix_fixable_pkg(self, pkg):
        return RET_NA

    def aix_fix_all(self):
        return RET_NA

    def aix_get_installed_packages(self):
        p = Popen(['lslpp', '-L', '-c'], stdout=PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            print >>sys.stderr, 'can not fetch installed packages list'
            return {}
        return self.aix_parse_lslpp(out)

    def aix_parse_lslpp(self, out):
        l = {}
        for line in out.split('\n'):
            if line.startswith('#') or len(line) == 0:
                continue
            v = line.split(':')
            if len(v) < 3:
                continue
            pkgname = v[1].replace('-'+v[2], '')
            if pkgname in l:
                l[pkgname] += [(v[2], "")]
            else:
                l[pkgname] = [(v[2], "")]
        return l

    def hp_fix_pkg(self, pkg):
        if self.check_pkg(pkg, verbose=False) == RET_OK:
            return RET_OK
        if self.fixable_pkg(pkg) == RET_ERR:
            return RET_ERR
        if self.highest_avail_version == "0":
            return RET_ERR
        if self.strict:
            self.fix_list.append(pkg["pkgname"]+',r='+pkg["minver"])
        else:
            self.fix_list.append(pkg["pkgname"]+',r='+self.highest_avail_version)
        self.need_pushpkg = True
        self.installed_packages = self.get_installed_packages()
        return RET_OK

    def hp_fix_all(self):
        r = call(['swinstall', '-x', 'allow_downdate=true', '-x', 'autoreboot=true', '-x', 'mount_all_filesystems=false', '-s', self.uri] + self.fix_list)
        if r != 0:
            return RET_ERR
        return RET_OK

    def hp_fixable_pkg(self, pkg):
        self.highest_avail_version = "0"
        if self.check_pkg(pkg, verbose=False) == RET_OK:
            return RET_OK
        cmd = ['swlist', '-l', self.pkg_type, '-s', self.uri, pkg['pkgname']]
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            if "not found on host" in err:
                print >>sys.stderr, '%s > %s not available in repositories'%(pkg['pkgname'], pkg['minver'])
            else:
                print >>sys.stderr, 'can not fetch available packages list'
            return RET_ERR
        l = self.hp_parse_swlist(out)
        if len(l) == 0:
            print >>sys.stderr, '%s > %s not available in repositories'%(pkg['pkgname'], pkg['minver'])
            return RET_ERR
        for v in map(lambda x: x[0], l.values()[0]):
            if V(v) > V(self.highest_avail_version):
                self.highest_avail_version = v
        if V(self.highest_avail_version) < V(pkg['minver']):
            print >>sys.stderr, '%s > %s not available in repositories'%(pkg['pkgname'], pkg['minver'])
            return RET_ERR
        return RET_OK

    def hp_get_installed_packages(self):
        p = Popen(['swlist', '-l', self.pkg_type], stdout=PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            print >>sys.stderr, 'can not fetch installed packages list'
            return {}
        return self.hp_parse_swlist(out)

    def hp_parse_swlist(self, out):
        l = {}
        for line in out.split('\n'):
            if line.startswith('#') or len(line) == 0:
                continue
            v = line.split()
            if len(v) < 2:
                continue
            if v[0] in l:
                l[v[0]] += [(v[1], "")]
            else:
                l[v[0]] = [(v[1], "")]
        return l

    def rpm_get_installed_packages(self):
        p = Popen(['rpm', '-qa', '--qf', '%{NAME} %{VERSION}-%{RELEASE} %{ARCH}\n'], stdout=PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            print >>sys.stderr, 'can not fetch installed packages list'
            return {}
        l = {}
        for line in out.split('\n'):
            v = line.split(' ')
            if len(v) != 3:
                continue
            if v[0] in l:
                l[v[0]] += [(v[1], v[2])]
            else:
                l[v[0]] = [(v[1], v[2])]
        return l

    def deb_get_installed_packages(self):
        p = Popen(['dpkg', '-l'], stdout=PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            print >>sys.stderr, 'can not fetch installed packages list'
            return {}
        l = {}
        for line in out.split('\n'):
            if not line.startswith('ii'):
                continue
            v = line.split()[1:3]
            l[v[0]] = [(v[1], "")]
        return l

    def apt_fixable_pkg(self, pkg):
        # TODO
        return RET_NA

    def zyp_fixable_pkg(self, pkg):
        return RET_NA

    def yum_fixable_pkg(self, pkg):
        try:
            r = self.check_pkg(pkg, verbose=False)
        except LiveKernVulnerable:
            r = RET_OK

        if r == RET_OK:
            return RET_OK

        cmd = ['yum', 'list', 'available', pkg['pkgname']]
        p = Popen(cmd, stdout=PIPE, stderr=PIPE)
        (out, err) = p.communicate()
        if p.returncode != 0:
            if "No matching Packages" in err:
                print >>sys.stderr, '%s > %s not available in repositories'%(pkg['pkgname'], pkg['minver'])
            else:
                print >>sys.stderr, 'can not fetch available packages list'
            return RET_ERR
        highest_avail_version = "0"
        for line in out.split('\n'):
            l = line.split()
            if len(l) != 3:
                continue
            if V(l[1]) > V(highest_avail_version):
                highest_avail_version = l[1]
        if V(highest_avail_version) < V(pkg['minver']):
            print >>sys.stderr, '%s > %s not available in repositories'%(pkg['pkgname'], pkg['minver'])
            return RET_ERR
        return RET_OK

    def zyp_fix_pkg(self, pkg):
        try:
            r = self.check_pkg(pkg, verbose=False)
        except LiveKernVulnerable:
            r = RET_OK
        if r == RET_OK:
            return RET_OK
        if self.fixable_pkg(pkg) == RET_ERR:
            return RET_ERR
        r = call(['zypper', 'install', '-y', pkg["pkgname"]])
        if r != 0:
            return RET_ERR
        self.need_pushpkg = True
        self.installed_packages = self.get_installed_packages()
        return RET_OK

    def yum_fix_pkg(self, pkg):
        try:
            r = self.check_pkg(pkg, verbose=False)
        except LiveKernVulnerable:
            r = RET_OK
        if r == RET_OK:
            return RET_OK
        if self.fixable_pkg(pkg) == RET_ERR:
            return RET_ERR
        r = call(['yum', '-y', 'install', pkg["pkgname"]])
        if r != 0:
            return RET_ERR
        self.need_pushpkg = True
        self.installed_packages = self.get_installed_packages()
        return RET_OK

    def apt_fix_pkg(self, pkg):
        if self.check_pkg(pkg, verbose=False) == RET_OK:
            return RET_OK
        r = call(['apt-get', 'install', '-y', '--allow-unauthenticated', pkg["pkgname"]])
        if r != 0:
            return RET_ERR
        self.need_pushpkg = True
        self.installed_packages = self.get_installed_packages()
        return RET_OK

    def get_raw_kver(self):
        return os.uname()[2]

    def get_kver(self):
        s = self.get_raw_kver()
        s = s.replace('xen', '')
        s = s.replace('hugemem', '')
        s = s.replace('smp', '')
        s = s.replace('PAE', '')
	s = s.replace('.x86_64','')
	s = s.replace('.i686','')
        return s

    def workaround_python_cmp(self, s):
        """ python list cmp says a > 9, but rpm says z < 0, ie :
            python says 2.6.18-238.el5 > 2.6.18-238.11.1.el5
            which is wrong in the POV of the package manager.

            replace .[a-z]* by .00000000[a-z] to force the desired behaviour
        """
        return re.sub("\.[a-zA-Z]+", repl, s)

    def check_pkg(self, pkg, verbose=True):
        if not pkg["pkgname"] in self.installed_packages:
            if verbose:
                print pkg["pkgname"], "is not installed (%s:not applicable)"%pkg["rule"]
            return RET_OK

        name = pkg["pkgname"]

        if name.startswith("kernel"):
            kver = self.get_raw_kver()
            for i in ('xen', 'hugemem', 'smp', 'PAE'):
                if kver.endswith(i) and name != "kernel-"+i:
                    if verbose:
                        print name, "bypassed :", i, "kernel booted", "(%s:not applicable)"%pkg["rule"]
                    return RET_OK

        r = RET_OK
        max = "0"
        max_v = V(max)
        ok = []
        minver = self.workaround_python_cmp(pkg['minver'])
        target = V(minver)
        if 'firstver' in pkg and pkg['firstver'] != "":
            firstver = self.workaround_python_cmp(pkg['firstver'])
        else:
            firstver = "0"
        firstver_v = V(firstver)
        candidates = map(lambda x: [name]+list(x), self.installed_packages[name])

        for _name, vers, arch in candidates:
            _vers = self.workaround_python_cmp(vers)
            actual = V(_vers)
            if actual > max_v or max == "0":
                max = vers
                max_v = actual
            if target <= actual or firstver_v > actual:
                ok.append((_name, vers, arch))

        if max == "0":
            # not installed
            if verbose:
                print name, "is not installed (%s:not applicable)"%pkg["rule"]
            return RET_OK

        if name.startswith("kernel"):
            kver = self.get_kver()
            if len(ok) == 0:
                if verbose:
                    print >>sys.stderr, ', '.join(map(lambda x: x[0]+"-"+x[1]+"."+x[2], candidates)), 'installed and vulnerable. upgrade to', pkg["minver"], "(%s:need upgrade)"%pkg["rule"]
                return RET_ERR
            elif kver not in map(lambda x: x[1], ok):
                if verbose:
                    print >>sys.stderr, ', '.join(map(lambda x: x[0]+"-"+x[1]+"."+x[2], ok)), "installed and not vulnerable but vulnerable kernel", self.get_raw_kver(), "booted", "(%s:need reboot)"%pkg["rule"]
                raise LiveKernVulnerable()
            else:
                if verbose:
                    print "kernel", self.get_raw_kver(), "installed, booted and not vulnerable", "(%s:not vulnerable)"%pkg["rule"]
                return RET_OK

        if len(ok) > 0:
            if verbose:
                print "%s installed and not vulnerable (%s:not vulnerable)"%(', '.join(map(lambda x: x[0]+"-"+x[1]+"."+x[2], ok)), pkg["rule"])
            return RET_OK

        if verbose:
            print >>sys.stderr, 'package', name+"-"+vers, 'is vulnerable. upgrade to', pkg["minver"], "(%s:need upgrade)"%pkg["rule"]
        return RET_ERR

    def check(self):
        r = 0
        for pkg in self.packages:
            try:
                _r = self.check_pkg(pkg)
                r |= _r
            except LiveKernVulnerable:
                r |= RET_ERR
        return r

    def fix(self):
        r = 0
        for pkg in self.packages:
            r |= self.fix_pkg(pkg)
        if self.fix_all is not None and len(self.fix_list) > 0:
            self.fix_all()
        if self.need_pushpkg:
            self.pushpkg()
        return r

    def pushpkg(self):
        bin = '/opt/opensvc/bin/nodemgr'
        if which(bin) is None:
            return
        cmd = [bin, 'pushpkg']
        print ' '.join(cmd)
        p = Popen(cmd)
        p.communicate()

    def fixable(self):
        r = 0
        for pkg in self.packages:
            r |= self.fixable_pkg(pkg)
        return r

if __name__ == "__main__":
    syntax = """syntax:
      %s PREFIX check|fixable|fix [uri]"""%sys.argv[0]
    if len(sys.argv) not in (3, 4):
        print >>sys.stderr, "wrong number of arguments"
        print >>sys.stderr, syntax
        sys.exit(RET_ERR)
    try:
        if len(sys.argv) == 4:
            o = CompVuln(sys.argv[1], sys.argv[3])
        else:
            o = CompVuln(sys.argv[1])
        if sys.argv[2] == 'check':
            RET = o.check()
        elif sys.argv[2] == 'fix':
            RET = o.fix()
        elif sys.argv[2] == 'fixable':
            RET = o.fixable()
        else:
            print >>sys.stderr, "unsupported argument '%s'"%sys.argv[2]
            print >>sys.stderr, syntax
            RET = RET_ERR
    except NotApplicable:
        sys.exit(RET_NA)
    except:
        import traceback
        traceback.print_exc()
        sys.exit(RET_ERR)

    sys.exit(RET)

