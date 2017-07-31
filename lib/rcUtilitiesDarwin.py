from rcUtilities import call, qcall

def check_ping(addr, timeout=5, count=1):
    if ':' in addr:
        ping = 'ping6'
    else:
        ping = 'ping'
    cmd = [ping, '-c', repr(count),
                 '-W', repr(timeout),
                 '-t', repr(timeout),
                 addr]
    (ret, out, err) = call(cmd)
    if ret == 0:
        return True
    return False

def devs_to_disks(self, devs=set()):
    return devs
