imports => unittest
exports (makeXORShift)

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

def makeXORShift(seed :Int ? (seed != 0)) as DeepFrozen:
    var s0 :Int := seed >> 64
    var s1 :Int := seed & 0xffffffffffffffff

    return object XORShift:
        to getAlgorithm() :Str:
            return "XORshift+"

        to getEntropy():
            var x := s0
            def y := s1
            s0 := y
            x ^= x << 23
            x ^= x >> 17
            x ^= y ^ (y  >> 26)
            s1 := x
            return [64, (x + y) & 0xffffffffffffffff]


def testXORShift(assert):
    # Vectors generated by a C implementation.
    def x := makeXORShift(1)
    assert.equal(x.getEntropy()[1], 0x0000000000000002)
    assert.equal(x.getEntropy()[1], 0x0000000000800041)
    assert.equal(x.getEntropy()[1], 0x0000000000800041)
    assert.equal(x.getEntropy()[1], 0x0000400000801002)
    assert.equal(x.getEntropy()[1], 0x0000800000902041)

unittest([testXORShift])
