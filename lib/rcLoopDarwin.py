import os
import re
import plistlib

from rcGlobalEnv import *
from rcUtilities import call, which
import rcStatus
import rcExceptions as ex

def file_to_loop(f):
    """Given a file path, returns the disk device associated. For example,
    /path/to/file => /dev/disk0s1
    """
    if which('hdiutil') is None:
        return []
    if not os.path.isfile(f):
        return []
    (ret, out, err) = call(['hdiutil', 'info', '-plist'])
    if ret != 0:
        return []

    devs= []
    try:
        pl = plistlib.readPlistFromString(out)
    except AttributeError as exc:
        raise ex.excError(str(exc))
    for image in pl['images']:
        if image.get('image-path') == f:
            for se in image['system-entities']:
                diskdevice = se.get('dev-entry')
                if diskdevice is not None:
                    devs.append(diskdevice)
                else:
                    return []

    return devs
