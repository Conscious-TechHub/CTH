# encoding=UTF-8

# Copyright © 2011-2022 Jakub Wilk <jwilk@jwilk.net>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

'''
Monkey-patch distutils to normalize metadata in generated archives:
- ownership (root:root),
- permissions (0644 or 0755),
- order of directory entries (sorted),
- tar format (ustar).

To enable normalization opportunistically, add this to setup.py:

   try:
       import distutils644
   except ImportError:
       pass
   else:
       distutils644.install()
'''

import contextlib
import distutils.archive_util  # pylint: disable=deprecated-module
import functools
import os
import stat
import sys
import tarfile
import types

if 'wheel.bdist_wheel' in sys.modules:
    import wheel.bdist_wheel
else:
    wheel = None

_ = {0}  # Python >= 2.7 is required
if (3, 0) <= sys.version_info < (3, 2):
    raise ImportError('Python 2.7 or 3.2+ is required')

@contextlib.contextmanager
def monkeypatch(mod, name, func):
    orig_func = getattr(mod, name)
    try:
        setattr(mod, name, func)
        yield
    finally:
        setattr(mod, name, orig_func)
        func.original = None

_orig_os_listdir = os.listdir
def os_listdir(path):
    return sorted(_orig_os_listdir(path))

_orig_os_walk = os.walk
def os_walk(*args, **kwargs):
    for dirpath, dirnames, filenames in _orig_os_walk(*args, **kwargs):
        dirnames.sort()
        filenames.sort()
        yield dirpath, dirnames, filenames

def normalize_mode(mode):
    mode &= ~0o77
    mode |= 0o644
    if mode & 0o100:
        mode |= 0o111
    return mode

class StatResult644(object):

    def __init__(self, original):
        self._original = original

    @property
    def st_mode(self):
        return normalize_mode(self._original.st_mode)

    @property
    def st_uid(self):
        return 0

    @property
    def st_gid(self):
        return 0

    def __getattr__(self, attr):
        return getattr(self._original, attr)

    def __getitem__(self, n):
        if n == stat.ST_MODE:
            return self.st_mode
        elif n in {stat.ST_UID, stat.ST_GID}:
            return 0
        return self._original[n]

_orig_os_lstat = os.lstat
def os_lstat(path):
    st = _orig_os_lstat(path)
    return StatResult644(st)

_orig_os_stat = os.stat
def os_stat(path):
    st = _orig_os_stat(path)
    return StatResult644(st)

if wheel is not None:
    try:
        _orig_archive_wheelfile = wheel.bdist_wheel.archive_wheelfile
    except AttributeError:
        archive_wheelfile = None
    else:
        @functools.wraps(_orig_archive_wheelfile)
        def archive_wheelfile(*args, **kwargs):
            with monkeypatch(os, 'stat', os_stat):
                return _orig_archive_wheelfile(*args, **kwargs)
    if archive_wheelfile is None:
        def _fix_last_zipinfo(zipfile, mode=None):
            zipinfo = zipfile.filelist[-1]
            if mode is None:
                mode = normalize_mode(zipinfo.external_attr >> 16)
            zipinfo.external_attr = (zipinfo.external_attr & 0xFFFF) | (mode << 16)
        _orig_WheelFile_write = wheel.wheelfile.WheelFile.write  # pylint: disable=no-member
        @functools.wraps(_orig_WheelFile_write)
        def WheelFile_write(self, *args, **kwargs):
            _orig_WheelFile_write(self, *args, **kwargs)
            _fix_last_zipinfo(self)
        _orig_WheelFile_writestr = wheel.wheelfile.WheelFile.writestr  # pylint: disable=no-member
        @functools.wraps(_orig_WheelFile_writestr)
        def WheelFile_writestr(self, *args, **kwargs):
            _orig_WheelFile_writestr(self, *args, **kwargs)
            _fix_last_zipinfo(self, mode=0o100644)

def install():

    class TarFile644(tarfile.TarFile):
        format = tarfile.USTAR_FORMAT

    def make_tarball(*args, **kwargs):
        orig_sys_modules = sys.modules.copy()
        tarfile_mod = types.ModuleType('tarfile644')
        tarfile_mod.open = TarFile644.open
        sys.modules['tarfile'] = tarfile_mod
        try:
            with monkeypatch(os, 'listdir', os_listdir), \
              monkeypatch(os, 'lstat', os_lstat):
                return distutils.archive_util.make_tarball(*args, **kwargs)
        finally:
            sys.modules.clear()
            sys.modules.update(orig_sys_modules)

    def make_zipfile(*args, **kwargs):
        with monkeypatch(os, 'walk', os_walk), \
          monkeypatch(os, 'stat', os_stat):
            return distutils.archive_util.make_zipfile(*args, **kwargs)

    def patch_format(fmt):
        func = fmt[0]
        if func is distutils.archive_util.make_tarball:
            func = make_tarball
        elif func is distutils.archive_util.make_zipfile:
            func = make_zipfile
        return (func,) + fmt[1:]

    archive_formats = distutils.archive_util.ARCHIVE_FORMATS
    archive_formats = {
        key: patch_format(value)
        for key, value
        in archive_formats.items()
    }
    distutils.archive_util.ARCHIVE_FORMATS = archive_formats

    if wheel is not None:
        if archive_wheelfile is not None:
            wheel.bdist_wheel.archive_wheelfile = archive_wheelfile
        else:
            wheel.wheelfile.WheelFile.write = WheelFile_write  # pylint: disable=no-member
            wheel.wheelfile.WheelFile.writestr = WheelFile_writestr  # pylint: disable=no-member

__version__ = '0.4'

__all__ = ['install']

# vim:ts=4 sts=4 sw=4 et
