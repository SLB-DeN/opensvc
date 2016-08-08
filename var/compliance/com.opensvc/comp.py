#!/usr/bin/env python

import sys
import os
import re
import json
import urllib2, base64

RET_OK = 0
RET_ERR = 1
RET_NA = 2

RET = RET_OK

class NotApplicable(Exception):
     pass

class Unfixable(Exception):
     pass

class ComplianceError(Exception):
     pass

class InitError(Exception):
     pass

class EndRecursion(Exception):
     pass

class CompObject(object):
    def __init__(self,
                 prefix=None,
                 data={}):
        if prefix:
            self.prefix = prefix.upper()
        elif "default_prefix" in data:
            self.prefix = data["default_prefix"].upper()
        else:
            self.prefix = "MAGIX12345"

        self.extra_syntax_parms = data.get("extra_syntax_parms")
        self.example_value = data.get("example_value", "")
        self.example_kwargs = data.get("example_kwargs", {})
        self.example_env = data.get("example_env", {})
        self.description = data.get("description", "(no description)")
        self.form_definition = data.get("form_definition", "(no form definition)")
        self.init_done = False

    def __getattribute__(self, s):
        if not object.__getattribute__(self, "init_done") and s in ("check", "fix", "fixable"):
            object.__setattr__(self, "init_done", True)
            object.__getattribute__(self, "init")()
        return object.__getattribute__(self, s)

    def init(self):
        pass

    def test(self):
        self.__init__(**self.example_kwargs)
        self.prefix = "OSVC_COMP_CO_TEST"
        for k, v in self.example_env.items():
            os.environ[k] = v
        os.environ[self.prefix] = self.example_value
        return self.check()

    def info(self):
        def indent(text):
            lines = text.split("\n")
            return "\n".join(["    "+line for line in lines])
        s = ""
        s += "Description\n"
        s += "===========\n"
        s += "\n"
        s += indent(self.description)+"\n"
        s += "\n"
        s += "Example rule\n"
        s += "============\n"
        s += "\n"
        s += indent(json.dumps(json.loads(self.example_value), indent=4, separators=(',', ': ')))+"\n"
        s += "\n"
        s += "Form definition\n"
        s += "===============\n"
        s += "\n"
        s += indent(self.form_definition)+"\n"
        s += "\n"
        print s

    def set_prefix(self, prefix):
        self.prefix = prefix.upper()

    def get_rules_raw(self):
        rules = []
        for k in [key for key in os.environ if key.startswith(self.prefix)]:
            s = self.subst(os.environ[k])
            rules += [s]
        if len(rules) == 0:
            raise NotApplicable("no rules (%s)" % self.prefix)
        return rules

    def get_rules(self):
        return [v[1] for v in self.get_rule_items()]

    def get_rule_items(self):
        rules = []
        for k in [key for key in os.environ if key.startswith(self.prefix)]:
            s = self.subst(os.environ[k])
            try:
                data = json.loads(s)
            except ValueError:
                print >>sys.stderr, 'failed to concatenate', os.environ[k], 'to rules list'
            if type(data) == list:
                for d in data:
                    rules += [(k, d)]
            else:
                rules += [(k, data)]
        if len(rules) == 0:
            raise NotApplicable("no rules (%s)" % self.prefix)
        return rules

    def subst(self, v):
        """
          A rule value can contain references to other rules as %%ENV:OTHER%%.
          This function substitutes these markers with the referenced rules values,
          which may themselves contain references. Hence the recursion.
        """
        max_recursion = 10

        if type(v) == list:
            l = []
            for _v in v:
                l.append(self.subst(_v))
            return l
        if type(v) != str and type(v) != unicode:
            return v

        p = re.compile('%%ENV:\w+%%', re.IGNORECASE)

        def _subst(v):
            matches = p.findall(v)
            if len(matches) == 0:
                raise EndRecursion
            for m in matches:
                s = m.strip("%").upper().replace('ENV:', '')
                if s in os.environ:
                    _v = os.environ[s]
                elif 'OSVC_COMP_'+s in os.environ:
                    _v = os.environ['OSVC_COMP_'+s]
                else:
                    _v = ""
                    raise NotApplicable()
                v = v.replace(m, _v)
            return v

        for i in range(max_recursion):
            try:
                v = _subst(v)
            except EndRecursion:
                break

        return v

    def collector_api(self):
        if hasattr(self, "collector_api_cache"):
            return self.collector_api_cache
        import platform
        sysname, nodename, x, x, machine, x = platform.uname()
        try:
            import ConfigParser
        except ImportError:
            import configparser as ConfigParser
        config = ConfigParser.RawConfigParser({})
        if os.path.realpath(__file__).startswith("/opt/opensvc"):
            config.read("/opt/opensvc/etc/node.conf")
        else:
            config.read("/etc/opensvc/node.conf")
        data = {}
        data["username"] = nodename
        data["password"] = config.get("node", "uuid")
        data["url"] = config.get("node", "dbopensvc").replace("/feed/default/call/xmlrpc", "/init/rest/api")
        self.collector_api_cache = data
        return self.collector_api_cache

    def collector_url(self):
        api = self.collector_api()
        s = "%s:%s@" % (api["username"], api["password"])
        url = api["url"].replace("https://", "https://"+s)
        url = url.replace("http://", "http://"+s)
        return url

    def collector_request(self, path):
        api = self.collector_api()
        url = api["url"]
        request = urllib2.Request(url+path)
        base64string = base64.encodestring('%s:%s' % (api["username"], api["password"])).replace('\n', '')
        request.add_header("Authorization", "Basic %s" % base64string)
        return request

    def collector_rest_get(self, path):
        api = self.collector_api()
        request = self.collector_request(path)
        if api["url"].startswith("https"):
            try:
                import ssl
                kwargs = {"context": ssl._create_unverified_context()}
            except:
                kwargs = {}
        else:
            raise ComplianceError("refuse to submit auth tokens through a non-encrypted transport")
        try:
            f = urllib2.urlopen(request, **kwargs)
        except urllib2.HTTPError as e:
            try:
                err = json.loads(e.read())["error"]
                e = ComplianceError(err)
            except:
                pass
            raise e
        import json
        data = json.loads(f.read())
        f.close()
        return data

    def collector_rest_get_to_file(self, path, fpath):
        api = self.collector_api()
        request = self.collector_request(path)
        if api["url"].startswith("https"):
            try:
                import ssl
                kwargs = {"context": ssl._create_unverified_context()}
            except:
                kwargs = {}
        else:
            raise ComplianceError("refuse to submit auth tokens through a non-encrypted transport")
        try:
            f = urllib2.urlopen(request, **kwargs)
        except urllib2.HTTPError as e:
            try:
                err = json.loads(e.read())["error"]
                e = ComplianceError(err)
            except:
                pass
            raise e
        with open(fpath, 'wb') as df:
            for chunk in iter(lambda: f.read(4096), b""):
                df.write(chunk)
        f.close()

    def collector_safe_uri_to_uuid(self, uuid):
        if uuid.startswith("safe://"):
            uuid = uuid.replace("safe://", "")
        if not uuid.startswith("safe"):
            raise ComplianceError("malformed safe file uri: %s" % uuid)
        return uuid

    def collector_safe_file_download(self, uuid, fpath):
        uuid = self.collector_safe_uri_to_uuid(uuid)
        self.collector_rest_get_to_file("/safe/" + uuid + "/download", fpath)

    def collector_safe_file_get_meta(self, uuid):
        uuid = self.collector_safe_uri_to_uuid(uuid)
        data = self.collector_rest_get("/safe/" + uuid)
        if len(data["data"]) == 0:
            raise ComplianceError(uuid + ": metadata not found")
        return data["data"][0]

    def urlretrieve(self, url, fpath):
        request = urllib2.Request(url)
        kwargs = {}
        if sys.hexversion >= 0x02070900:
            import ssl
            kwargs["context"] = ssl._create_unverified_context()
        f = urllib2.urlopen(request, **kwargs)
        with open(fpath, 'wb') as df:
            for chunk in iter(lambda: f.read(4096), b""):
                df.write(chunk)

    def md5(self, fpath):
        import hashlib
        hash = hashlib.md5()
        with open(fpath, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash.update(chunk)
        return hash.hexdigest()


def main(co):
    syntax =  "syntax:\n"
    syntax += """ %s <ENV VARS PREFIX> check|fix|fixable\n"""%sys.argv[0]
    syntax += """ %s test|info"""%sys.argv[0]

    try:
        o = co()
    except NotApplicable as e:
        print e
        sys.exit(RET_NA)
    if o.extra_syntax_parms:
        syntax += " "+o.extra_syntax_parms

    if len(sys.argv) == 2:
        if sys.argv[1] == 'test':
            try:
                RET = o.test()
                sys.exit(RET)
            except ComplianceError as e:
                print >>sys.stderr, e
                sys.exit(RET_ERR)
            except NotApplicable:
                sys.exit(RET_NA)
        elif sys.argv[1] == 'info':
            o.info()
            sys.exit(0)

    if len(sys.argv) < 3:
        print >>sys.stderr, syntax
        sys.exit(RET_ERR)

    argv = [sys.argv[1]]
    if len(sys.argv) > 3:
        argv += sys.argv[3:] 
    o.__init__(*argv)
    try:
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
    except ComplianceError as e:
        print >>sys.stderr, e
        sys.exit(RET_ERR)
    except NotApplicable as e:
        print e
        sys.exit(RET_NA)
    except:
        import traceback
        traceback.print_exc()
        sys.exit(RET_ERR)

    sys.exit(RET)

if __name__ == "__main__":
    print >>sys.stderr, "this file is for import into compliance objects"

