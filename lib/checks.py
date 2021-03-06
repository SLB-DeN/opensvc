from __future__ import print_function
from rcGlobalEnv import rcEnv
from rcUtilities import mimport
import os

class check(object):
    undef = [{
              'path': '',
              'instance': 'undef',
              'value': '-1'
             }]
    def __init__(self, svcs=[]):
        self.svcs = svcs
        if self.svcs is None:
            self.svcs = []

    def do_check(self): # pragma: no cover
        """
        To be implemented by child classes.
        """
        return []

class checks(check):
    check_list = []

    def __init__(self, svcs=[]):
        self.svcs = svcs
        self.node = None
        self.register('Fs', 'Usage')
        self.register('Fs', 'Inode')
        self.register('Vg', 'Usage')
        self.register('Eth')
        self.register('Lag')
        self.register('Mpath')
        self.register('Mpath', 'Powerpath')
        self.register('Zfs', 'Usage')
        self.register('Raid', 'Smart', 'Array')
        self.register('Raid', 'Mega', 'Raid')
        self.register('Raid', 'Sas2')
        self.register('Fm', 'Fmadm')
        self.register('Fm', 'Open', 'Manage')
        self.register('Mce')
        self.register('Zpool')
        self.register('Btrfs', 'Dev', 'Stats')
        self.register('Advfs', 'Usage')
        self.register('Numa')
        self.register('Sync')
        self.register('Jstat')
        self.register_local_checkers()

    def __iadd__(self, c):
        if isinstance(c, check):
            self.check_list.append(c)
        elif isinstance(c, checks):
            self.check_list += c.check_list
        return self

    def register_local_checkers(self):
        import os
        import glob
        check_d = os.path.join(rcEnv.paths.pathvar, 'check')
        if not os.path.exists(check_d):
            return
        import sys
        sys.path.append(check_d)
        for f in glob.glob(os.path.join(check_d, 'check*.py')):
            if rcEnv.sysname not in f:
                continue
            cname = os.path.basename(f).replace('.py', '')
            try:
                m = __import__(cname)
                self += m.check(svcs=self.svcs)
            except Exception as e:
                print('Could not import check:', cname, file=sys.stderr)
                print(e, file=sys.stderr)

    def register(self, *args):
        try:
            m = mimport("check", *args)
        except ImportError:
            return
        self += m.check(svcs=self.svcs)

    def do_checks(self):
        import datetime

        now = str(datetime.datetime.now())
        data = {}
        vars = [\
            "chk_nodename",
            "chk_svcname",
            "chk_type",
            "chk_instance",
            "chk_value",
            "chk_updated"]
        vals = []

        for chk in self.check_list:
            idx = chk.chk_type
            if hasattr(chk, "chk_name"):
                driver = chk.chk_name.lower()
            else:
                driver = "generic"

            _data = chk.do_check()

            if not isinstance(_data, (list, tuple)) or len(_data) == 0:
                continue

            instances = []
            for instance in _data:
                if not isinstance(instance, dict):
                    continue
                if 'instance' not in instance:
                    continue
                if instance['instance'] == 'undef':
                    continue
                if 'value' not in instance:
                    continue
                _instance = {
                    "instance": instance.get("instance", ""),
                    "value": instance.get("value", ""),
                    "path": instance.get("path", ""),
                    "driver": driver,
                }

                vals.append([\
                    rcEnv.nodename,
                    _instance["path"],
                    chk.chk_type,
                    _instance['instance'],
                    str(_instance['value']).replace("%",""),
                    now]
                )

                instances.append(_instance)

            if len(instances) > 0:    
                if idx not in data:
                    data[idx] = instances
                else:
                    data[idx] += instances

        self.node.collector.call('push_checks', vars, vals)
        if self.node.options.format is None:
            self.print_checks(data)
            return
        return data

    def print_checks(self, data):
        from forest import Forest
        from rcColor import color
        tree = Forest()
        head_node = tree.add_node()
        head_node.add_column(rcEnv.nodename, color.BOLD)
        for chk_type, instances in data.items():
            node = head_node.add_node()
            node.add_column(chk_type, color.BROWN)
            for instance in instances:
                _node = node.add_node()
                _node.add_column(str(instance["instance"]), color.LIGHTBLUE)
                _node.add_column(instance["path"])
                _node.add_column(str(instance["value"]))
                if instance["driver"] == "generic":
                    _node.add_column()
                else:
                    _node.add_column(instance["driver"])
        tree.out()

