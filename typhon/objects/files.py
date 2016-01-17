# Copyright (C) 2015 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import os

from rpython.rlib.objectmodel import specialize
from rpython.rlib.rarithmetic import intmask
from rpython.rtyper.lltypesystem.lltype import scoped_alloc
from rpython.rtyper.lltypesystem.rffi import charpsize2str

from typhon import log, rsodium, ruv
from typhon.atoms import getAtom
from typhon.autohelp import autohelp
from typhon.enum import makeEnum
from typhon.errors import Refused, userError
from typhon.objects.constants import NullObject
from typhon.objects.data import BytesObject, StrObject, unwrapBytes, unwrapStr
from typhon.objects.refs import LocalResolver, makePromise
from typhon.objects.root import Object, runnable
from typhon.vats import currentVat, scopedVat


ABORTFLOW_0 = getAtom(u"abortFlow", 0)
FLOWINGFROM_1 = getAtom(u"flowingFrom", 1)
FLOWABORTED_1 = getAtom(u"flowAborted", 1)
FLOWSTOPPED_1 = getAtom(u"flowStopped", 1)
FLOWTO_1 = getAtom(u"flowTo", 1)
GETCONTENTS_0 = getAtom(u"getContents", 0)
OPENDRAIN_0 = getAtom(u"openDrain", 0)
OPENFOUNT_0 = getAtom(u"openFount", 0)
PAUSEFLOW_0 = getAtom(u"pauseFlow", 0)
RECEIVE_1 = getAtom(u"receive", 1)
SETCONTENTS_1 = getAtom(u"setContents", 1)
SIBLING_1 = getAtom(u"sibling", 1)
RENAME_1 = getAtom(u"rename", 1)
RUN_1 = getAtom(u"run", 1)
RUN_2 = getAtom(u"run", 2)
STOPFLOW_0 = getAtom(u"stopFlow", 0)
TEMPORARYSIBLING_0 = getAtom(u"temporarySibling", 0)
UNPAUSE_0 = getAtom(u"unpause", 0)


@autohelp
class FileUnpauser(Object):
    """
    A pause on a file fount.
    """

    def __init__(self, fount):
        self.fount = fount

    def recv(self, atom, args):
        if atom is UNPAUSE_0:
            if self.fount is not None:
                self.fount.unpause()
                # Let go so that the fount can be GC'd if necessary.
                self.fount = None
            return NullObject

        raise Refused(self, atom, args)


def readCB(fs):
    # Does *not* invoke user code.
    try:
        size = intmask(fs.c_result)
        with ruv.unstashingFS(fs) as (vat, fount):
            assert isinstance(fount, FileFount)
            # Done with fs, but don't free it; it belongs to the fount.
            if size > 0:
                data = charpsize2str(fount.buf.c_base, size)
                fount.receive(data)
            elif size < 0:
                msg = ruv.formatError(size).decode("utf-8")
                fount.abort(u"libuv error: %s" % msg)
            else:
                fount.stop(u"End of file")
    except:
        print "Exception in readCB"


@autohelp
class FileFount(Object):
    """
    A fount for a file.
    """

    pauses = 0
    pos = 0

    def __init__(self, fs, fd, vat):
        self.fs = fs
        self.fd = fd
        self.vat = vat

        # XXX read size should be tunable
        self.buf = ruv.allocBuf(16384)

        # Set this up only once.
        ruv.stashFS(self.fs, (self.vat, self))

    def recv(self, atom, args):
        if atom is FLOWTO_1:
            self.drain = drain = args[0]
            rv = drain.call(u"flowingFrom", [self])
            self.queueRead()
            return rv

        if atom is PAUSEFLOW_0:
            return self.pause()

        if atom is STOPFLOW_0:
            self.stop(u"stopFlow() called")
            return NullObject

        if atom is ABORTFLOW_0:
            self.abort(u"abortFlow() called")
            return NullObject

        raise Refused(self, atom, args)

    def stop(self, reason):
        from typhon.objects.collections.maps import EMPTY_MAP
        self.vat.sendOnly(self.drain, FLOWSTOPPED_1, [StrObject(reason)],
                          EMPTY_MAP)
        self.close()

    def abort(self, reason):
        from typhon.objects.collections.maps import EMPTY_MAP
        self.vat.sendOnly(self.drain, FLOWABORTED_1, [StrObject(reason)],
                          EMPTY_MAP)
        self.close()

    def close(self):
        uv_loop = self.vat.uv_loop
        ruv.fsClose(uv_loop, self.fs, self.fd, ruv.fsDiscard)
        ruv.freeBuf(self.buf)
        self.drain = None

    def pause(self):
        self.pauses += 1
        return FileUnpauser(self)

    def unpause(self):
        self.pauses -= 1
        if not self.pauses:
            self.queueRead()

    def queueRead(self):
        with scoped_alloc(ruv.rffi.CArray(ruv.buf_t), 1) as bufs:
            bufs[0].c_base = self.buf.c_base
            bufs[0].c_len = self.buf.c_len
            ruv.fsRead(self.vat.uv_loop, self.fs, self.fd, bufs, 1, self.pos,
                       readCB)

    def receive(self, data):
        from typhon.objects.collections.maps import EMPTY_MAP
        # Advance the file pointer.
        self.pos += len(data)
        self.vat.sendOnly(self.drain, RECEIVE_1, [BytesObject(data)],
                          EMPTY_MAP)
        self.queueRead()


def writeCB(fs):
    try:
        with ruv.unstashingFS(fs) as (vat, drain):
            assert isinstance(drain, FileDrain)
            size = intmask(fs.c_result)
            if size > 0:
                drain.written(size)
            elif size < 0:
                msg = ruv.formatError(size).decode("utf-8")
                drain.abort(u"libuv error: %s" % msg)
    except:
        print "Exception in writeCB"


@autohelp
class FileDrain(Object):
    """
    A drain for a file.
    """

    fount = None
    pos = 0

    # State machine:
    # * READY: Bufs are empty.
    # * WRITING: Bufs are partially full. Write is pending.
    # * BUSY: Bufs are overfull. Write is pending.
    # * CLOSING: Bufs are partially full. Write is pending. New writes cause
    #   exceptions.
    # * CLOSED: Bufs are empty. New writes cause exceptions.
    READY, WRITING, BUSY, CLOSING, CLOSED = makeEnum(u"FileDrain",
        u"ready writing busy closing closed".split())
    _state = READY

    def __init__(self, fs, fd, vat):
        self.fs = fs
        self.fd = fd
        self.vat = vat

        self.bufs = []

        # Set this up only once.
        ruv.stashFS(self.fs, (self.vat, self))

    def recv(self, atom, args):
        if atom is FLOWINGFROM_1:
            self.fount = args[0]
            return self

        if atom is RECEIVE_1:
            data = unwrapBytes(args[0])
            self.receive(data)
            return NullObject

        if atom is FLOWSTOPPED_1:
            # Prepare to shut down. Switch to CLOSING to stop future data from
            # being queued.
            self.closing()
            return NullObject

        if atom is FLOWABORTED_1:
            # We'll shut down cleanly, but we're going to discard all the work
            # that we haven't yet written.
            self.bufs = []
            self.closing()
            return NullObject

        raise Refused(self, atom, args)

    def receive(self, data):
        if self._state in (self.READY, self.WRITING, self.BUSY):
            self.bufs.append(data)

            if self._state is self.READY:
                # We're not writing right now, so queue a write.
                self.queueWrite()
                self._state = self.WRITING
        else:
            raise userError(u"Can't write to drain in state %s" %
                            self._state.repr)

    def abort(self, reason):
        if self.fount is not None:
            with scopedVat(self.vat):
                from typhon.objects.collections.maps import EMPTY_MAP
                self.vat.sendOnly(self.fount, ABORTFLOW_0, [], EMPTY_MAP)
        self.closing()

    def queueWrite(self):
        with ruv.scopedBufs(self.bufs) as bufs:
            ruv.fsWrite(self.vat.uv_loop, self.fs, self.fd, bufs,
                        len(self.bufs), self.pos, writeCB)

    def closing(self):
        if self._state is self.READY:
            # Optimization: proceed directly to CLOSED if there's no
            # outstanding writes.
            self.queueClose()
        self._state = self.CLOSING

    def queueClose(self):
        ruv.fsClose(self.vat.uv_loop, self.fs, self.fd, ruv.fsDiscard)
        self._state = self.CLOSED

    def written(self, size):
        self.pos += size
        bufs = []
        for buf in self.bufs:
            if size >= len(buf):
                size -= len(buf)
            elif size == 0:
                bufs.append(buf)
            else:
                assert size >= 0
                bufs.append(buf[size:])
                size = 0
        self.bufs = bufs

        if self.bufs:
            # More bufs remain to write. Queue them.
            self.queueWrite()
            # If we were CLOSING before, we're still CLOSING now. Otherwise,
            # we transition (from READY/WRITING) to WRITING.
            if self._state is not self.CLOSING:
                self._state = self.WRITING
        elif self._state is self.CLOSING:
            # Finally, we're out of things to do. Request a close.
            self.queueClose()
        else:
            # We are ready for more work.
            self._state = self.READY


def openFountCB(fs):
    # Does *not* run user-level code. The scoped vat is only for promise
    # resolution.
    try:
        fd = intmask(fs.c_result)
        vat, r = ruv.unstashFS(fs)
        assert isinstance(r, LocalResolver)
        with scopedVat(vat):
            if fd < 0:
                msg = ruv.formatError(fd).decode("utf-8")
                r.smash(StrObject(u"Couldn't open file fount: %s" % msg))
                # Done with fs.
                ruv.fsDiscard(fs)
            else:
                r.resolve(FileFount(fs, fd, vat))
    except:
        print "Exception in openFountCB"


def openDrainCB(fs):
    # As above.
    try:
        fd = intmask(fs.c_result)
        vat, r = ruv.unstashFS(fs)
        assert isinstance(r, LocalResolver)
        with scopedVat(vat):
            if fd < 0:
                msg = ruv.formatError(fd).decode("utf-8")
                r.smash(StrObject(u"Couldn't open file drain: %s" % msg))
                # Done with fs.
                ruv.fsDiscard(fs)
            else:
                r.resolve(FileDrain(fs, fd, vat))
    except:
        print "Exception in openDrainCB"


class GetContents(Object):
    """
    Struct used to manage getContents/0 calls.

    Has to be an Object so that it can be unified with LocalResolver.
    No, seriously.
    """

    # Our position reading from the file.
    pos = 0

    def __init__(self, vat, fs, fd, resolver):
        self.vat = vat
        self.fs = fs
        self.fd = fd
        self.resolver = resolver

        self.pieces = []

        # XXX read size should be tunable
        self.buf = ruv.allocBuf(16384)

        # Do our initial stashing.
        ruv.stashFS(fs, (vat, self))

    def append(self, data):
        self.pieces.append(data)
        self.pos += len(data)
        # Queue another!
        ruv.stashFS(self.fs, (self.vat, self))
        self.queueRead()

    def succeed(self):
        # Clean up libuv stuff.
        ruv.fsClose(self.vat.uv_loop, self.fs, self.fd, ruv.fsDiscard)

        # Finally, resolve.
        buf = "".join(self.pieces)
        self.resolver.resolve(BytesObject(buf))

    def fail(self, reason):
        # Clean up libuv stuff.
        ruv.fsClose(self.vat.uv_loop, self.fs, self.fd, ruv.fsDiscard)

        # And resolve.
        self.resolver.smash(StrObject(u"libuv error: %s" % reason))

    def queueRead(self):
        with scoped_alloc(ruv.rffi.CArray(ruv.buf_t), 1) as bufs:
            bufs[0].c_base = self.buf.c_base
            bufs[0].c_len = self.buf.c_len
            ruv.fsRead(self.vat.uv_loop, self.fs, self.fd, bufs, 1, self.pos,
                       getContentsCB)

def openGetContentsCB(fs):
    try:
        fd = intmask(fs.c_result)
        vat, r = ruv.unstashFS(fs)
        assert isinstance(r, LocalResolver)
        with scopedVat(vat):
            if fd < 0:
                msg = ruv.formatError(fd).decode("utf-8")
                r.smash(StrObject(u"Couldn't open file fount: %s" % msg))
                # Done with fs.
                ruv.fsDiscard(fs)
            else:
                # Strategy: Read and use the callback to queue additional reads
                # until done. This call is known to its caller to be expensive, so
                # there's not much point in trying to be clever about things yet.
                gc = GetContents(vat, fs, fd, r)
                gc.queueRead()
    except:
        print "Exception in openGetContentsCB"

def getContentsCB(fs):
    try:
        size = intmask(fs.c_result)
        # Don't use with-statements here; instead, each next action in
        # GetContents will re-stash if necessary. ~ C.
        vat, self = ruv.unstashFS(fs)
        assert isinstance(self, GetContents)
        if size > 0:
            data = charpsize2str(self.buf.c_base, size)
            self.append(data)
        elif size < 0:
            msg = ruv.formatError(size).decode("utf-8")
            self.fail(msg)
        else:
            # End of file! Complete the callback.
            self.succeed()
    except Exception:
        print "Exception in getContentsCB"


def renameCB(fs):
    try:
        success = intmask(fs.c_result)
        vat, r = ruv.unstashFS(fs)
        if success < 0:
            msg = ruv.formatError(success).decode("utf-8")
            r.smash(StrObject(u"Couldn't rename file: %s" % msg))
        else:
            r.resolve(NullObject)
        # Done with fs.
        ruv.fsDiscard(fs)
    except:
        print "Exception in renameCB"


class SetContents(Object):

    pos = 0

    def __init__(self, vat, data, resolver, src, dest):
        self.vat = vat
        self.data = data
        self.resolver = resolver
        self.src = src
        self.dest = dest

    def fail(self, reason):
        self.resolver.smash(StrObject(reason))

    def queueWrite(self):
        with ruv.scopedBufs([self.data]) as bufs:
            ruv.fsWrite(self.vat.uv_loop, self.fs, self.fd, bufs,
                        1, self.pos, writeSetContentsCB)

    def startWriting(self, fd, fs):
        self.fd = fd
        self.fs = fs
        self.queueWrite()

    def written(self, size):
        self.pos += size
        self.data = self.data[size:]
        if self.data:
            self.queueWrite()
        else:
            # Finished writing; let's move on to the rename.
            ruv.fsClose(self.vat.uv_loop, self.fs, self.fd,
                        closeSetContentsCB)

    def rename(self):
        # And issuing the rename is surprisingly straightforward.
        p = self.src.rename(self.dest.asBytes())
        self.resolver.resolve(p)
        # At last, done with fs. No need to unstash; it was already unstashed
        # in the callback. (We're being called *from the callback*.)
        ruv.fsDiscard(self.fs)


def openSetContentsCB(fs):
    try:
        fd = intmask(fs.c_result)
        with ruv.unstashingFS(fs) as (vat, sc):
            assert isinstance(sc, SetContents)
            if fd < 0:
                msg = ruv.formatError(fd).decode("utf-8")
                sc.fail(u"Couldn't open file fount: %s" % msg)
                # Done with fs.
                ruv.fsDiscard(fs)
            else:
                sc.startWriting(fd, fs)
    except:
        print "Exception in openSetContentsCB"


def writeSetContentsCB(fs):
    try:
        with ruv.unstashingFS(fs) as (vat, sc):
            assert isinstance(sc, SetContents)
            size = intmask(fs.c_result)
            if size > 0:
                sc.written(size)
            elif size < 0:
                msg = ruv.formatError(size).decode("utf-8")
                sc.fail(u"libuv error: %s" % msg)
    except:
        print "Exception in writeSetContentsCB"


def closeSetContentsCB(fs):
    try:
        vat, sc = ruv.unstashFS(fs)
        # Need to scope vat here.
        with scopedVat(vat):
            assert isinstance(sc, SetContents)
            size = intmask(fs.c_result)
            if size < 0:
                msg = ruv.formatError(size).decode("utf-8")
                sc.fail(u"libuv error: %s" % msg)
            else:
                # Success.
                sc.rename()
    except:
        print "Exception in closeSetContentsCB"


@autohelp
class FileResource(Object):
    """
    A Resource which provides access to the file system of the current
    process.
    """

    # For help understanding this class, consult FilePath, the POSIX
    # standards, and a bottle of your finest and strongest liquor. Perhaps not
    # in that order, though.

    _immutable_ = True
    _immutable_fields_ = "segments[*]",

    def __init__(self, segments):
        self.segments = segments

    def toString(self):
        return u"<file resource %s>" % self.asBytes().decode("utf-8")

    def asBytes(self):
        return "/".join(self.segments)

    @specialize.call_location()
    def open(self, callback, flags=None, mode=None):
        # Always call this as .open(callback, flags=..., mode=...)
        assert flags is not None
        assert mode is not None

        p, r = makePromise()
        vat = currentVat.get()
        uv_loop = vat.uv_loop
        fs = ruv.alloc_fs()

        path = self.asBytes()
        log.log(["fs"], u"makeFileResource: Opening file '%s'" % path.decode("utf-8"))
        ruv.stashFS(fs, (vat, r))
        ruv.fsOpen(uv_loop, fs, path, flags, mode, callback)
        return p

    def rename(self, dest):
        p, r = makePromise()
        vat = currentVat.get()
        uv_loop = vat.uv_loop
        fs = ruv.alloc_fs()

        src = self.asBytes()
        ruv.stashFS(fs, (vat, r))
        ruv.fsRename(uv_loop, fs, src, dest, renameCB)
        return p

    def sibling(self, segment):
        return FileResource(self.segments[:-1] + [segment])

    def temporarySibling(self, suffix):
        fileName = rsodium.randomHex() + suffix
        return self.sibling(fileName)

    def recv(self, atom, args):
        if atom is GETCONTENTS_0:
            return self.open(openGetContentsCB, flags=os.O_RDONLY, mode=0000)

        if atom is SETCONTENTS_1:
            data = unwrapBytes(args[0])
            sibling = self.temporarySibling(".setContents")

            p, r = makePromise()
            vat = currentVat.get()
            uv_loop = vat.uv_loop
            fs = ruv.alloc_fs()

            path = sibling.asBytes()
            # Use CREAT | EXCL to cause a failure if the temporary file
            # already exists.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            sc = SetContents(vat, data, r, sibling, self)
            ruv.stashFS(fs, (vat, sc))
            ruv.fsOpen(uv_loop, fs, path, flags, 0777, openSetContentsCB)
            return p

        if atom is OPENFOUNT_0:
            return self.open(openFountCB, flags=os.O_RDONLY, mode=0000)

        if atom is OPENDRAIN_0:
            # Create the file if it doesn't yet exist, and truncate it if it
            # does. Trust the umask to be reasonable for now.
            flags = os.O_CREAT | os.O_WRONLY
            # XXX this behavior should be configurable via namedarg?
            flags |= os.O_TRUNC
            return self.open(openDrainCB, flags=flags, mode=0777)

        if atom is RENAME_1:
            fr = args[0]
            if not isinstance(fr, FileResource):
                raise userError(u"rename/1: Must be file resource")
            return self.rename(fr.asBytes())

        if atom is SIBLING_1:
            name = unwrapStr(args[0])
            if u'/' in name:
                raise userError(u"sibling/1: Illegal file name '%s'" % name)
            return self.sibling(name.encode("utf-8"))

        if atom is TEMPORARYSIBLING_0:
            return self.temporarySibling(".new")

        raise Refused(self, atom, args)


@runnable(RUN_1)
def makeFileResource(path):
    """
    Make a file Resource.
    """

    path = unwrapStr(path)
    segments = [segment.encode("utf-8") for segment in path.split(u'/')]
    if not path.startswith(u'/'):
        # Relative path.
        segments = os.getcwd().split('/') + segments
        log.log(["fs"], u"makeFileResource.run/1: Relative path '%s'" % path)
    return FileResource(segments)
