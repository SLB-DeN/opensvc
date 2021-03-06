from rcUtilities import justcall, which, cache
from rcGlobalEnv import rcEnv

def listpkg():
    lines = list_pkg_info()
    lines += list_pkg_query()
    return lines

@cache("pkg_info")
def list_pkg_info():
    if which('pkg_info') is None:
        return []
    cmd = ['pkg_info']
    out, err, ret = justcall(cmd)
    lines = []
    for line in out.splitlines():
        l = line.split()
        if len(l) < 2:
            continue
        nv = l[0].split('-')
        version = nv[-1]
        pkgname = '-'.join(nv[0:-1])
        x = [rcEnv.nodename, pkgname, version, '']
        lines.append(x)
    return lines

@cache("pkg_query")
def list_pkg_query():
    if which('pkg') is None:
        return []
    cmd = ['pkg', 'query', '-a', '%n;;%v;;%q']
    out, err, ret = justcall(cmd)
    lines = []
    for line in out.splitlines():
        l = line.split(';;')
        if len(l) < 3:
            continue
        x = [rcEnv.nodename] + l
        lines.append(x)
    return lines

def listpatch():
    return []

