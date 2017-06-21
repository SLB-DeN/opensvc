from __future__ import print_function
import os, sys
import datetime
import time
import logging
import socket
import select
import shlex
import re
import rcExceptions as ex
from subprocess import *
from rcGlobalEnv import rcEnv
from functools import wraps
import lock
import json
import ast
import operator as op

# supported operators in arithmetic expressions
operators = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.BitXor: op.xor,
    ast.USub: op.neg,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Not: op.not_,
    ast.Eq: op.eq,
    ast.NotEq: op.ne,
    ast.Lt: op.lt,
    ast.LtE: op.le,
    ast.Gt: op.gt,
    ast.GtE: op.ge,
    ast.In: op.contains,
}

def eval_expr(expr):
    """ arithmetic expressions evaluator
    """
    def eval_(node):
        if isinstance(node, ast.Num): # <number>
            return node.n
        elif isinstance(node, ast.Str):
            return node.s
        elif isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Tuple):
            return tuple(node.elts)
        elif isinstance(node, ast.BinOp): # <left> <operator> <right>
            return operators[type(node.op)](eval_(node.left), eval_(node.right))
        elif isinstance(node, ast.UnaryOp): # <operator> <operand> e.g., -1
            return operators[type(node.op)](eval_(node.operand))
        elif isinstance(node, ast.BoolOp):  # Boolean operator: either "and" or "or" with two or more values
            if type(node.op) == ast.And:
                return all(eval_(val) for val in node.values)
            else:  # Or:
                for val in node.values:
                    result = eval_(val)
                    if result:
                        return result
                    return result  # or returns the final value even if it's falsy
        elif isinstance(node, ast.Compare):  # A comparison expression, e.g. "3 > 2" or "5 < x < 10"
            left = eval_(node.left)
            for comparison_op, right_expr in zip(node.ops, node.comparators):
                right = eval_(right_expr)
                if type(comparison_op) == ast.In:
                    if isinstance(right, tuple):
                        if not any(q.id == left for q in right if isinstance(q, ast.Name)):
                            return False
                    else:
                        if not operators[type(comparison_op)](right, left):
                            return False
                else:
                    if not operators[type(comparison_op)](left, right):
                        return False
                left = right
                return True
        else:
            raise TypeError(node)
    return eval_(ast.parse(expr, mode='eval').body)


PROTECTED_DIRS = [
    '/',
    '/bin',
    '/boot',
    '/dev',
    '/dev/pts',
    '/dev/shm',
    '/home',
    '/opt',
    '/proc',
    '/sys',
    '/tmp',
    '/usr',
    '/var',
]

if os.name == 'nt':
    close_fds = False
else:
    close_fds = True

def lazy(fn):
    """
    A decorator for on-demand initialization of a property
    """
    attr_name = '_lazy_' + fn.__name__
    @property
    def _lazyprop(self):
        if not hasattr(self, attr_name):
            setattr(self, attr_name, fn(self))
        return getattr(self, attr_name)
    return _lazyprop

def lazy_initialized(self, attr):
    """
    Return True if the lazy property has been initialized
    """
    attr_name = '_lazy_' + attr
    if hasattr(self, attr_name):
        return True
    return False

def set_lazy(self, attr, value):
    """
    Set a <value> as the <self> object lazy property hidden property value
    """
    attr_name = '_lazy_' + attr
    setattr(self, attr_name, value)

def unset_lazy(self, attr):
    """
    Unset <attr> lazy property hidden property, iow flush the cache
    """
    attr_name = '_lazy_' + attr
    if hasattr(self, attr_name):
        delattr(self, attr_name)

def bdecode(buff):
    if sys.version_info[0] < 3:
        return buff
    if type(buff) == str:
        return buff
    else:
        try:
            return str(buff, "utf-8")
        except:
            return str(buff, "ascii")
    return buff

def is_string(s):
    """ python[23] compatible
    """
    if sys.version_info[0] == 2:
        l = (str, unicode)
    else:
        l = (str)
    if isinstance(s, l):
        return True
    return False

def mimport(*args, **kwargs):
    def fmt(s):
        if len(s) > 1:
            return s[0].upper()+s[1:].lower()
        elif len(s) == 1:
            return s[0].upper()
        else:
            return ""

    mod = ""
    for i, e in enumerate(args):
        if e in ("res", "prov") and i == 0:
            mod += e
        else:
            mod += fmt(e)

    try:
        return __import__(mod+rcEnv.sysname)
    except ImportError:
        pass

    try:
        return __import__(mod)
    except ImportError:
        pass

    if kwargs.get("fallback", True) and len(args) > 1:
        args = args[:-1]
        return mimport(*args, **kwargs)
    else:
        raise ImportError("no module found")

def ximport(base):
    mod = base + rcEnv.sysname
    try:
        m = __import__(mod)
        return m
    except:
        pass

    return __import__(base)

def check_privs():
    if os.name == 'nt':
        return
    if os.getuid() != 0:
        import copy
        l = copy.copy(sys.argv)
        l[0] = os.path.basename(l[0]).replace(".py", "")
        print('Insufficient privileges. Try:\n sudo ' + ' '.join(l))
        sys.exit(1)

def banner(text, ch='=', length=78):
    spaced_text = ' %s ' % text
    banner = spaced_text.center(length, ch)
    return banner

def is_exe(fpath):
    """Returns True if file path is executable, False otherwize
    does not follow symlink
    """
    if os.path.isdir(fpath):
        return False
    return os.path.exists(fpath) and os.access(fpath, os.X_OK)

def which(program):
    if program is None:
        return
    def ext_candidates(fpath):
        yield fpath
        for ext in os.environ.get("PATHEXT", "").split(os.pathsep):
            yield fpath + ext

    fpath, fname = os.path.split(program)
    if fpath:
        if os.path.isfile(program) and is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            for candidate in ext_candidates(exe_file):
                if is_exe(candidate):
                    return candidate

    return

def justcall(argv=['/bin/false']):
    """subprosses call argv, return (stdout,stderr,returncode)
    """
    if which(argv[0]) is None:
        return ("", "", 1)
    process = Popen(argv, stdout=PIPE, stderr=PIPE, close_fds=close_fds)
    stdout, stderr = process.communicate(input=None)
    return bdecode(stdout), bdecode(stderr), process.returncode

def empty_string(buff):
    b = buff.strip(' ').strip('\n')
    if len(b) == 0:
        return True
    return False

def lcall(cmd, logger, outlvl=logging.INFO, errlvl=logging.ERROR, timeout=None, **kwargs):
    """
    Variant of subprocess.call that accepts a logger instead of stdout/stderr,
    and logs stdout messages via logger.debug and stderr messages via
    logger.error.
    """
    start = time.time()
    proc = Popen(cmd, stdout=PIPE, stderr=PIPE, **kwargs)
    log_level = {proc.stdout: outlvl, proc.stderr: errlvl}
    terminated = False
    killed = False

    def check_io():
        ready_to_read = select.select([proc.stdout, proc.stderr], [], [], 1000)[0]
        for io in ready_to_read:
            line = io.readline()
            if sys.version_info[0] < 3:
                line = line.decode("utf8")
            else:
                line = line.decode("ascii", errors="ignore")
            if line in ('', b''):
                continue
            logger.log(log_level[io], line[:-1])

    # keep checking stdout/stderr until the proc exits
    while proc.poll() is None:
        check_io()
        ellapsed = time.time() - start
        if timeout and ellapsed > timeout:
            if not terminated:
                logger.error("execution timeout (%d seconds). send SIGTERM." % timeout)
                proc.terminate()
                terminated = True
            elif not killed and ellapsed > timeout*2:
                logger.error("SIGTERM handling timeout (%d seconds). send SIGKILL." % timeout)
                proc.kill()
                killed = True

    check_io()  # check again to catch anything after the process exits

    return proc.wait()

def call(argv,
         cache=False,      # serve/don't serve cmd output from cache
         log=None,         # callers should provide there own logger
                           # or we'll have to allocate a generic one

         info=False,       # False: log cmd as debug
                           # True:  log cmd as info

         outlog=False,     # False: discard stdout

         errlog=True,      # False: discard stderr
                           # True:  log stderr as err, warn or info
                           #        depending on err_to_warn and
                           #        err_to_info value

         outdebug=True,    # True:  log.debug cmd stdout
                           # False: skip log.debug stdout

         errdebug=True,    # True:  log.debug cmd stderr
                           # False: skip log.debug stderr
                           #        depending on err_to_warn and
                           #        err_to_info value
         err_to_warn=False,
         err_to_info=False,
         warn_to_info=False,
         shell=False,
         preexec_fn=None,
         cwd=None,
         env=None):
    """ return(ret, out,err)
    """
    if log is None:
        log = logging.getLogger('CALL')

    if not argv or len(argv) == 0:
        return (0, '', '')

    if shell:
        cmd = argv
    else:
        cmd = ' '.join(argv)
     
    if not shell and which(argv[0]) is None:
        log.error("%s does not exist or not in path or is not executable"%
                  argv[0])
        return (1, '', '')

    if info:
        log.info(cmd)
    else:
        log.debug(cmd)

    if not hasattr(rcEnv, "call_cache"):
        rcEnv.call_cache = {}

    if cache and cmd not in rcEnv.call_cache:
        log.debug("cache miss for '%s'"%cmd)

    if not cache or cmd not in rcEnv.call_cache:
        process = Popen(argv, stdout=PIPE, stderr=PIPE, close_fds=close_fds, shell=shell, preexec_fn=preexec_fn, cwd=cwd, env=env)
        buff = process.communicate()
        buff = tuple(map(lambda x: bdecode(x), buff))
        ret = process.returncode
        if ret == 0:
            log.debug("store '%s' output in cache"%cmd)
            rcEnv.call_cache[cmd] = buff
        elif cmd in rcEnv.call_cache:
            log.debug("discard '%s' output from cache because ret!=0"%cmd)
            del rcEnv.call_cache[cmd]
        elif cache:
            log.debug("skip store '%s' output in cache because ret!=0"%cmd)
    else:
        log.debug("serve '%s' output from cache"%cmd)
        buff = rcEnv.call_cache[cmd]
        ret = 0
    if not empty_string(buff[1]):
        if err_to_info:
            log.info('stderr:\n' + buff[1])
        elif err_to_warn:
            log.warning('stderr:\n' + buff[1])
        elif errlog:
            if ret != 0:
                log.error('stderr:\n' + buff[1])
            elif warn_to_info:
                log.info('command successful but stderr:\n' + buff[1])
            else:
                log.warning('command successful but stderr:\n' + buff[1])
        elif errdebug:
            log.debug('stderr:\n' + buff[1])
    if not empty_string(buff[0]):
        if outlog:
            if ret == 0:
                log.info('output:\n' + buff[0])
            elif err_to_info:
                log.info('command failed with stdout:\n' + buff[0])
            elif err_to_warn:
                log.warning('command failed with stdout:\n' + buff[0])
            else:
                log.error('command failed with stdout:\n' + buff[0])
        elif outdebug:
            log.debug('output:\n' + buff[0])

    return (ret, buff[0], buff[1])

def qcall(argv=['/bin/false']) :
    """qcall Launch Popen it args disgarding output and stderr"""
    if not argv:
        return (0, '')
    process = Popen(argv, stdout=PIPE, stderr=PIPE, close_fds=close_fds)
    process.wait()
    return process.returncode

def vcall(args, **kwargs):
    kwargs["info"] = True
    kwargs["outlog"] = True
    return call(args, **kwargs)

def getmount(path):
    path = os.path.abspath(path)
    while path != os.path.sep:
        if not os.path.islink(path) and os.path.ismount(path):
            return path
        path = os.path.abspath(os.path.join(path, os.pardir))
    return path

def protected_dir(path):
    path = path.rstrip("/")
    if path in PROTECTED_DIRS:
        return True
    return False

def protected_mount(path):
    mount = getmount(path)
    if mount in PROTECTED_DIRS:
        return True
    return False

def printplus(obj):
    """
    Pretty-prints the object passed in.

    """
    # Dict
    if isinstance(obj, dict):
        for k, v in sorted(obj.items()):
            print("%s: %s" % (str(k), str(v)))

    # List or tuple
    elif isinstance(obj, list) or isinstance(obj, tuple):
        for x in obj:
            print(x)

    # Other
    else:
        print(obj)

def cmdline2list(cmdline):
    """
    Translate a command line string into a list of arguments, using
    using the same rules as the MS C runtime:

    1) Arguments are delimited by white space, which is either a
       space or a tab.

    2) A string surrounded by double quotation marks is
       interpreted as a single argument, regardless of white space
       contained within.  A quoted string can be embedded in an
       argument.

    3) A double quotation mark preceded by a backslash is
       interpreted as a literal double quotation mark.

    4) Backslashes are interpreted literally, unless they
       immediately precede a double quotation mark.

    5) If backslashes immediately precede a double quotation mark,
       every pair of backslashes is interpreted as a literal
       backslash.  If the number of backslashes is odd, the last
       backslash escapes the next double quotation mark as
       described in rule 3.
    """

    # See
    # http://msdn.microsoft.com/library/en-us/vccelng/htm/progs_12.asp

    # Step 1: Translate all literal quotes into QUOTE.  Justify number
    # of backspaces before quotes.
    tokens = []
    bs_buf = ""
    QUOTE = 1 # \", literal quote
    for c in cmdline:
        if c == '\\':
            bs_buf += c
        elif c == '"' and bs_buf:
            # A quote preceded by some number of backslashes.
            num_bs = len(bs_buf)
            tokens.extend(["\\"] * (num_bs//2))
            bs_buf = ""
            if num_bs % 2:
                # Odd.  Quote should be placed literally in array
                tokens.append(QUOTE)
            else:
                # Even.  This quote serves as a string delimiter
                tokens.append('"')

        else:
            # Normal character (or quote without any preceding
            # backslashes)
            if bs_buf:
                # We have backspaces in buffer.  Output these.
                tokens.extend(list(bs_buf))
                bs_buf = ""

            tokens.append(c)

    # Step 2: split into arguments
    result = [] # Array of strings
    quoted = False
    arg = [] # Current argument
    tokens.append(" ")
    for c in tokens:
        if c == '"':
            # Toggle quote status
            quoted = not quoted
        elif c == QUOTE:
            arg.append('"')
        elif c in (' ', '\t'):
            if quoted:
                arg.append(c)
            else:
                # End of argument.  Output, if anything.
                if arg:
                    result.append(''.join(arg))
                    arg = []
        else:
            # Normal character
            arg.append(c)

    return result

def action_triggers(self, trigger="", action=None, **kwargs):
    """
    Executes a service or resource trigger. Guess if the shell mode is needed
    from the trigger syntax.
    """

    actions = [
        'provision',
        'unprovision',
        'start',
        'stop',
        'shutdown',
        'sync_nodes',
        'sync_drp',
        'sync_all',
        'sync_resync',
        'sync_update',
        'sync_restore',
        'command', # tasks use that as an action
    ]

    compat_triggers = [
        'pre_syncnodes', 'pre_syncdrp',
        'post_syncnodes', 'post_syncdrp',
        'post_syncresync', 'pre_syncresync',
        'post_syncupdate', 'pre_syncupdate',
    ]

    def get_trigger_cmdv(cmd, kwargs):
        """
        Return the cmd arg useable by subprocess Popen
        """
        if not kwargs.get("shell", False):
            if sys.version_info[0] < 3:
                cmdv = shlex.split(cmd.encode('utf8'))
                cmdv = [elem.decode('utf8') for elem in cmdv]
            else:
                cmdv = shlex.split(cmd)
        else:
            cmdv = cmd
        return cmdv

    if hasattr(self, "svc"):
        svc = self.svc
        section = self.rid
    else:
        svc = self
        section = "DEFAULT"

    if action not in actions:
        return
    elif action == "startstandby":
        action = "start"
    elif action == "shutdown":
        action = "stop"

    if "blocking" in kwargs:
        blocking = kwargs["blocking"]
        del kwargs["blocking"]
    else:
        blocking = False

    if trigger == "":
        attr = action
    else:
        attr = trigger+"_"+action

    # translate deprecated actions
    if attr in compat_triggers:
        attr = compat_triggers[attr]

    try:
        cmd = svc.conf_get_string_scope(section, attr, use_default=False)
    except ex.OptNotFound:
        return

    if "|" in cmd or "&&" in cmd or ";" in cmd:
        kwargs["shell"] = True

    cmdv = get_trigger_cmdv(cmd, kwargs)

    self.log.info("%s: %s", attr, cmd)

    if svc.options.dry_run:
        return

    try:
        ret = self.lcall(cmdv, **kwargs)
    except OSError as exc:
        ret = 1
        if exc.errno == 8:
            self.log.error("%s exec format error: check the script shebang", cmd)
        else:
            self.log.error("%s error: %s", cmd, str(exc))
    except Exception as exc:
        ret = 1
        self.log.error("%s error: %s", cmd, str(exc))

    if blocking and ret != 0:
        raise ex.excError("%s trigger %s blocking error" % (trigger, cmd))


def try_decode(string, codecs=['utf8', 'latin1']):
    for i in codecs:
        try:
            return string.decode(i)
        except:
            pass
    return string

def getaddr_cache_set(name, addr):
    cache_d = os.path.join(rcEnv.paths.pathvar, "cache", "addrinfo")
    if not os.path.exists(cache_d):
        os.makedirs(cache_d)
    cache_f = os.path.join(cache_d, name)
    with open(cache_f, 'w') as f:
        f.write(addr)
    return addr

def getaddr_cache_get(name):
    cache_d = os.path.join(rcEnv.paths.pathvar, "cache", "addrinfo")
    if not os.path.exists(cache_d):
        os.makedirs(cache_d)
    cache_f = os.path.join(cache_d, name)
    if not os.path.exists(cache_f):
        raise Exception("addrinfo cache empty for name %s" % name)
    cache_mtime = datetime.datetime.fromtimestamp(os.stat(cache_f).st_mtime)
    limit_mtime = datetime.datetime.now() - datetime.timedelta(minutes=16)
    if cache_mtime < limit_mtime:
        raise Exception("addrinfo cache expired for name %s (%s)" % (name, cache_mtime.strftime("%Y-%m-%d %H:%M:%S")))
    with open(cache_f, 'r') as f:
        addr = f.read()
    if addr.count(".") != 3 and ":" not in addr:
        raise Exception("addrinfo cache corrupted for name %s: %s" % (name, addr))
    return addr

def getaddr(name, cache_fallback, log=None):
    if cache_fallback:
        return getaddr_caching(name, log=log)
    else:
        return getaddr_non_caching(name)

def getaddr_non_caching(name, log=None):
    a = socket.getaddrinfo(name, None)
    if len(a) == 0:
        raise Exception("could not resolve name %s: empty dns request resultset" % name)
    addr = a[0][4][0]
    try:
        getaddr_cache_set(name, addr)
    except Exception as e:
        if log:
            log.warning("failed to cache name addr %s, %s: %s"  %(name, addr, str(e)))
    return addr

def getaddr_caching(name, log=None):
    try:
        addr = getaddr_non_caching(name)
    except Exception as e:
        if log:
            log.warning("%s. fallback to cache." % str(e))
        addr = getaddr_cache_get(name)
    if log:
        log.info("fetched %s address for name %s from cache" % (addr, name))
    return addr

def convert_bool(s):
    if str(s).lower() in ("yes", "y", "true",  "t", "1"):
        return True
    if str(s).lower() in ("no",  "n", "false", "f", "0", "0.0", "", "none", "[]", "{}"):
        return False
    raise Exception('Invalid value for boolean conversion: ' + str(s))

def convert_size(s, _to='', _round=1):
    l = ['', 'K', 'M', 'G', 'T', 'P', 'Z', 'E']
    if type(s) in (int, float):
        s = str(s)
    s = s.strip().replace(",", ".")
    if len(s) == 0:
        return 0
    if s == '0':
        return 0
    size = s
    unit = ""
    for i, c in enumerate(s):
        if not c.isdigit() and c != '.':
            size = s[:i]
            unit = s[i:].strip()
            break
    if 'i' in unit:
        factor = 1000
    else:
        factor = 1024
    if len(unit) > 0:
        unit = unit[0].upper()
    size = float(size)

    try:
        start_idx = l.index(unit)
    except:
        raise Exception("unsupported unit in converted value: %s" % s)

    for i in range(start_idx):
        size *= factor

    if 'i' in _to:
        factor = 1000
    else:
        factor = 1024
    if len(_to) > 0:
        unit = _to[0].upper()
    else:
        unit = ''

    if unit == 'B':
        unit = ''

    try:
        end_idx = l.index(unit)
    except:
        raise Exception("unsupported target unit: %s" % unit)

    for i in range(end_idx):
        size /= factor

    size = int(size)
    d = size % _round
    if d > 0:
        size = (size // _round) * _round
    return size

def cidr_to_dotted(s):
    i = int(s)
    _in = ""
    _out = ""
    for i in range(i):
        _in += "1"
    for i in range(32-i):
        _in += "0"
    _out += str(int(_in[0:8], 2))+'.'
    _out += str(int(_in[8:16], 2))+'.'
    _out += str(int(_in[16:24], 2))+'.'
    _out += str(int(_in[24:32], 2))
    return _out

def to_dotted(s):
    s = str(s)
    if '.' in s:
        return s
    return cidr_to_dotted(s)

def hexmask_to_dotted(mask):
    mask = mask.replace('0x', '')
    s = [str(int(mask[i:i+2], 16)) for i in range(0, len(mask), 2)]
    return '.'.join(s)

def dotted_to_cidr(mask):
    if mask is None:
        return ''
    cnt = 0
    l = mask.split(".")
    l = map(lambda x: int(x), l)
    for a in l:
        cnt += str(bin(a)).count("1")
    return str(cnt)

def to_cidr(s):
    if '.' in s:
        return dotted_to_cidr(s)
    elif re.match("^(0x)*[0-9a-f]{8}$", s):
        # example: 0xffffff00
        s = hexmask_to_dotted(s)
        return dotted_to_cidr(s)
    return s

def term_width():
    default = 78
    try:
        # python 3.3+
        return os.get_terminal_size().columns
    except:
        pass
    if rcEnv.sysname == "Windows":
        return default
    if which("stty") is None:
        return default
    out, err, ret = justcall(['stty', '-a'])
    m = re.search('columns\s+(?P<columns>\d+);', out)
    if m:
        return int(m.group('columns'))
    try:
        return int(os.environ["COLUMNS"])
    except Exception as e:
        pass
    return default

def get_cache_d():
    return os.path.join(rcEnv.paths.pathvar, "cache", rcEnv.session_uuid)

def cache(sig):
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            if len(args) > 0 and hasattr(args[0], "log"):
                log = args[0].log
            else:
                log = None

            if len(args) > 0 and hasattr(args[0], "cache_sig_prefix"):
                _sig = args[0].cache_sig_prefix + sig
            else:
                _sig = sig

            fpath = cache_fpath(_sig)

            try:
                lfd = lock.lock(timeout=30, delay=0.1, lockfile=fpath+'.lock', intent="cache")
            except Exception as e:
                if log:
                    log.warning("cache locking error: %s. run command uncached." % str(e))
                return fn(*args, **kwargs)
            try:
                data = cache_get(fpath, log=log)
            except Exception as e:
                if log:
                    log.debug(str(e))
                data = fn(*args, **kwargs)
                cache_put(fpath, data, log=log)
            lock.unlock(lfd)
            return data
        return decorator
    return wrapper

def cache_fpath(sig):
    cache_d = get_cache_d()
    if not os.path.exists(cache_d):
        try:
            os.makedirs(cache_d)
        except:
            # we run unlocked here ...
            # another process created the dir since we tested ?
            pass
    fpath = os.path.join(cache_d, sig)
    return fpath

def cache_put(fpath, data, log=None):
    if log:
        log.debug("cache PUT: %s" % fpath)
    try:
        with open(fpath, "w") as f:
            json.dump(data, f)
    except Exception as e:
        try:
            os.unlink(fpath)
        except:
            pass
    return data

def cache_get(fpath, log=None):
    if not os.path.exists(fpath):
        raise Exception("cache MISS: %s" % fpath)
    if log:
        log.debug("cache GET: %s" % fpath)
    try:
        with open(fpath, "r") as f:
            data = json.load(f)
    except Exception as e:
        raise ex.excError("cache read error: %s" % str(e))
    return data

def clear_cache(sig, o=None):
    if o and hasattr(o, "cache_sig_prefix"):
        sig = o.cache_sig_prefix + sig
    fpath = cache_fpath(sig)
    if not os.path.exists(fpath):
        return
    if o and hasattr(o, "log"):
        o.log.debug("cache CLEAR: %s" % fpath)
    lfd = lock.lock(timeout=30, delay=0.1, lockfile=fpath+'.lock')
    try:
        os.unlink(fpath)
    except:
        pass
    lock.unlock(lfd)

def purge_cache():
    import time
    import shutil
    cache_d = os.path.join(rcEnv.paths.pathvar, "cache")
    if not os.path.exists(cache_d) or not os.path.isdir(cache_d):
        return
    for d in os.listdir(cache_d): 
        d = os.path.join(cache_d, d)
        if not os.path.isdir(d) or not os.stat(d).st_ctime < time.time()-(21600): 
            # session more recent than 6 hours
            continue
        try:
            shutil.rmtree(d)
        except:
            pass

def read_cf(fpath, defaults=None):
    """
    Read and parse an arbitrary ini-formatted config file, and return
    the RawConfigParser object.
    """
    import codecs
    from rcConfigParser import RawConfigParser
    try:
        from collections import OrderedDict
        config = RawConfigParser(dict_type=OrderedDict)
    except ImportError:
        config = RawConfigParser()

    if defaults is None:
        defaults = {}
    config = RawConfigParser(defaults)
    if not os.path.exists(fpath):
        return config
    with codecs.open(fpath, "r", "utf8") as ofile:
        if sys.version_info[0] >= 3:
            config.read_file(ofile)
        else:
            config.readfp(ofile)
    return config

if __name__ == "__main__":
    #print("call(('id','-a'))")
    #(r,output,err)=call(("/usr/bin/id","-a"))
    #print("status: ", r, "output:", output)
    print(convert_size("10000 KiB", _to='MiB', _round=3))
    print(convert_size("10M", _to='', _round=4096))

