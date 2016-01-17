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

import time

from rpython.rlib.debug import debug_print

from typhon.atoms import getAtom
from typhon.objects.collections.lists import ConstList
from typhon.objects.data import DoubleObject, IntObject, unwrapStr
from typhon.objects.root import runnable

RUN_2 = getAtom(u"run", 2)

@runnable(RUN_2)
def bench(obj, name):
    name = unwrapStr(name).encode("utf-8")

    if not benchmarkSettings.enabled:
        debug_print("Not running benchmark", name,
                    "since benchmarking is disabled")
        return ConstList([IntObject(0), DoubleObject(0.0)])

    debug_print("Benchmarking", name)

    # Step 1: Calibrate timing loop.
    debug_print("Calibrating timing loop...")
    # Unroll do-while iteration.
    loops = 1
    debug_print("Trying 1 loop...")
    taken = time.time()
    obj.call(u"run", [])
    taken = time.time() - taken
    while taken < 1.0 and loops < 100000000:
        debug_print("Took", taken, "seconds to run", loops, "loops")
        loops *= 10
        debug_print("Trying", loops, "loops...")
        acc = 0
        taken = time.time()
        while acc < loops:
            acc += 1
            obj.call(u"run", [])
        taken = time.time() - taken
    debug_print("Okay! Will take", loops, "loops at", taken, "seconds")

    # Step 2: Take trials.
    debug_print("Taking trials...")
    trialCount = 3 - 1
    # Unroll first iteration to get maximum.
    acc = 0
    taken = time.time()
    while acc < loops:
        acc += 1
        obj.call(u"run", [])
    taken = time.time() - taken
    result = taken
    while trialCount:
        trialCount -= 1
        acc = 0
        taken = time.time()
        while acc < loops:
            acc += 1
            obj.call(u"run", [])
        taken = time.time() - taken
        if taken < result:
            result = taken

    # All done!
    return ConstList([IntObject(loops), DoubleObject(taken)])


class BenchmarkSettings(object):

    enabled = True

    def disable(self):
        self.enabled = False


benchmarkSettings = BenchmarkSettings()
