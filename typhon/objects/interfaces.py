from typhon.atoms import getAtom
from typhon.autohelp import autohelp
from typhon.errors import Refused
from typhon.objects.constants import NullObject
from typhon.objects.collections.sets import ConstSet, monteSet
from typhon.objects.data import IntObject, StrObject
from typhon.objects.root import Object


GETARITY_0 = getAtom(u"getArity", 0)
GETDOCSTRING_0 = getAtom(u"getDocstring", 0)
GETMETHODS_0 = getAtom(u"getMethods", 0)
GETVERB_0 = getAtom(u"getVerb", 0)


@autohelp
class ComputedMethod(Object):
    """
    A method description.
    """

    _immutable_fields_ = "arity", "docstring", "verb"

    def __init__(self, arity, docstring, verb):
        self.arity = arity
        self.docstring = docstring
        self.verb = verb

    def toString(self):
        return u"<computed message %s/%d>" % (self.verb, self.arity)

    def recv(self, atom, args):
        if atom is GETARITY_0:
            return IntObject(self.arity)

        if atom is GETDOCSTRING_0:
            if self.docstring is not None:
                return StrObject(self.docstring)
            return NullObject

        if atom is GETVERB_0:
            return StrObject(self.verb)

        raise Refused(self, atom, args)


@autohelp
class ComputedInterface(Object):
    """
    An interface generated on the fly for an object.
    """

    _immutable_fields_ = "atoms[*]",

    def __init__(self, obj):
        self.atoms = obj.respondingAtoms()
        self.docstring = obj.docString()

    def toString(self):
        return u"<computed interface>"

    def recv(self, atom, args):
        if atom is GETDOCSTRING_0:
            if self.docstring is not None:
                return StrObject(self.docstring)
            return NullObject

        if atom is GETMETHODS_0:
            d = monteSet()
            for atom in self.atoms:
                d[ComputedMethod(atom.arity, None, atom.verb)] = None
            return ConstSet(d)

        raise Refused(self, atom, args)
