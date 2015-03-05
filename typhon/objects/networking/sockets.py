# Copyright (C) 2014 Google Inc. All rights reserved.  #
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

from typhon.atoms import getAtom
from typhon.errors import Refused, userError
from typhon.objects.collections import ConstList, unwrapList
from typhon.objects.constants import NullObject
from typhon.objects.data import IntObject, StrObject, unwrapInt, unwrapStr
from typhon.objects.root import Object
from typhon.vats import currentVat


FLOWINGFROM_1 = getAtom(u"flowingFrom", 1)
FLOWSTOPPED_1 = getAtom(u"flowStopped", 1)
FLOWTO_1 = getAtom(u"flowTo", 1)
PAUSEFLOW_0 = getAtom(u"pauseFlow", 0)
RECEIVE_1 = getAtom(u"receive", 1)
RUN_2 = getAtom(u"run", 2)
STOPFLOW_0 = getAtom(u"stopFlow", 0)
UNPAUSE_0 = getAtom(u"unpause", 0)


class SocketUnpauser(Object):
    """
    A pause on a socket fount.
    """

    def __init__(self, fount):
        self.fount = fount

    def recv(self, atom, args):
        if atom is UNPAUSE_0:
            if self.fount is not None:
                self.fount.unpause()
                self.fount = None
            return NullObject
        raise Refused(self, atom, args)


class SocketFount(Object):
    """
    A fount which flows data out from a socket.
    """

    pauses = 0
    buf = ""

    _drain = None

    def toString(self):
        return u"<SocketFount>"

    def recv(self, atom, args):
        if atom is FLOWTO_1:
            self._drain = drain = args[0]
            rv = drain.call(u"flowingFrom", [self])
            return rv

        if atom is PAUSEFLOW_0:
            return self.pause()

        if atom is STOPFLOW_0:
            self.terminate(u"Flow stopped")
            return NullObject

        raise Refused(self, atom, args)

    def pause(self):
        self.pauses += 1
        return SocketUnpauser(self)

    def unpause(self):
        self.pauses -= 1
        self.flush()

    def receive(self, buf):
        self.buf += buf
        self.flush()

    def flush(self):
        if not self.pauses and self._drain is not None:
            rv = [IntObject(ord(byte)) for byte in self.buf]
            vat = currentVat.get()
            vat.sendOnly(self._drain, RECEIVE_1, [ConstList(rv)])
            self.buf = ""

    def terminate(self, reason):
        if self._drain is not None:
            self._drain.call(u"flowStopped", [StrObject(reason)])
            # Release the drain. They should have released us as well.
            self._drain = None


class SocketDrain(Object):
    """
    A drain which sends received data out on a socket.
    """

    _closed = False

    def __init__(self, socket):
        self.sock = socket
        self._buf = []

    def toString(self):
        return u"<SocketDrain(%s)>" % self.sock.repr()

    def recv(self, atom, args):
        if atom is FLOWINGFROM_1:
            return self

        if atom is RECEIVE_1:
            if self._closed:
                raise userError(u"Can't send data to a closed socket!")

            data = unwrapList(args[0])
            s = "".join([chr(unwrapInt(byte)) for byte in data])
            self.sock._outbound.append(s)
            return NullObject

        if atom is FLOWSTOPPED_1:
            self._closed = True
            vat = currentVat.get()
            self.sock.error(vat._reactor, unwrapStr(args[0]))
            return NullObject

        raise Refused(self, atom, args)
