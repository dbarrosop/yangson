# Copyright © 2016 CZ.NIC, z. s. p. o.
#
# This file is part of Yangson.
#
# Yangson is free software: you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# Yangson is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License along
# with Yangson.  If not, see <http://www.gnu.org/licenses/>.

"""Instance data represented as a persistent structure.

This module implements the following classes:

* LinkedList: Persistent linked list of instance values.
* InstanceNode: Abstract class for instance nodes.
* RootNode: Root of the data tree.
* ObjectMember: Instance node that is an object member.
* ArrayEntry: Instance node that is an array entry.
* InstanceRoute: Route into an instance value.
* ResourceIdParser: Parser for RESTCONF resource identifiers.
* InstanceIdParser: Parser for instance identifiers.

The module defines the following exceptions:

* InstanceException: Base class for exceptions related to operations
  on instance nodes.
* InstanceValueError: The instance value is incompatible with the called method.
* NonexistentInstance: Attempt to access an instance node that doesn't
  exist.
"""

from datetime import datetime
from typing import Any, Callable, List, Tuple, Union
from urllib.parse import unquote
from .exceptions import YangsonException
from .context import Context
from .enumerations import ContentType, ValidationScope
from .instvalue import *
from .parser import EndOfInput, Parser, UnexpectedInput
from .typealiases import *
from .typealiases import _Singleton

__all__ = ["InstanceNode", "RootNode", "ObjectMember", "ArrayEntry",
               "InstanceIdParser", "ResourceIdParser", "InstanceRoute",
               "InstanceException", "InstanceValueError", "NonexistentInstance"]

class LinkedList:
    """Persistent linked list of instance values."""

    @classmethod
    def from_list(cls, vals: List[Value] = []) -> "LinkedList":
        """Create an instance from a standard list.

        Args:
            vals: Python list of instance values.
        """
        res = EmptyList()
        for v in vals[::-1]:
            res = res.cons(v)
        return res

    def __init__(self, head: Value, tail: "LinkedList"):
        """Initialize the class instance."""
        self.head = head
        """Head of the linked list."""
        self.tail = tail
        """Tail of the linked list."""

    def __bool__(self):
        """Return receiver's boolean value."""
        return True

    def __iter__(self):
        """Iterate over receiver's entries."""
        l = self
        while True:
            try:
                n, l = l.pop()
            except IndexError:
                raise StopIteration from None
            yield n

    def cons(self, val: Value) -> "LinkedList":
        """Prepend a value to the receiver in the persistent way.

        Args:
            val: Instance value.

        Returns: A new linked list.
        """
        return LinkedList(val, self)

    def pop(self) -> Tuple[Value, "LinkedList"]:
        """Deconstruct the receiver.

        Returns: A tuple with receiver's head and tail, respectively.
        """
        return (self.head, self.tail)

class EmptyList(LinkedList, metaclass=_Singleton):
    """Singleton class representing the empty linked list."""

    def __init__(self):
        pass

    def __bool__(self):
        return False

    def __getitem__(self, key):
        raise IndexError

    def pop(self) -> None:
        raise IndexError

class InstanceNode:
    """YANG data node instance implemented as a zipper structure."""

    def __init__(self, key: InstanceKey, value: Value, parinst: "InstanceNode",
                     schema_node: "DataNode", timestamp: datetime):
        """Initialize the class instance."""
        self.path = parinst.path + (key,)   # type: List[InstanceKey]
        """Path in the data tree."""
        self.parinst = parinst         # type: Optional["InstanceNode"]
        """Parent instance node, or ``None`` for the root node."""
        self.schema_node = schema_node # type: DataNode
        """Data node corresponding to the instance node."""
        self.timestamp = timestamp     # type: datetime
        """Time of the receiver's last modification."""
        self.value = value             # type: Value
        """Value of the receiver."""

    @property
    def namespace(self) -> Optional[YangIdentifier]:
        """The receiver's namespace."""
        return self.schema_node.ns

    def __str__(self) -> str:
        """Return string representation of the receiver's value."""
        sn = self.schema_node
        return (str(self.value) if isinstance(self.value, StructuredValue) else
                sn.type.canonical_string(self.value))

    def json_pointer(self) -> str:
        """Return JSON Pointer [RFC6901]_ of the receiver."""
        return "/" + "/".join([str(c) for c in self.path])

    def __getitem__(self, key: InstanceKey) -> "InstanceNode":
        """Return member or entry with the given key.

        Args:
            key: Entry index (for an array) or member name (for an object).

        Raises:
            NonexistentInstance: If receiver's value doesn't contain member
                `name`.
            InstanceValueError: If the receiver's value is not an object.
        """
        if isinstance(self.value, ObjectValue):
            return self._member(key)
        if isinstance(self.value, ArrayValue):
            return self._entry(key)
        raise InstanceValueError(self, "scalar instance")

    def __iter__(self):
        """Return receiver's iterator.

        Raises:
            InstanceValueError: if the receiver is a scalar value that cannot
                be iterated.
        """
        def it():
            try:
                en = self[0]
                while True:
                    yield en
                    en = en.next()
            except NonexistentInstance:
                raise StopIteration from None
        if isinstance(self.value, ArrayValue):
            return it()
        if isinstance(self.value, ObjectValue):
            return iter(self.value)
        raise InstanceValueError(self, "scalar instance")

    def is_internal(self) -> bool:
        """Return ``True`` if the receiver is an instance of an internal node.
        """
        return isinstance(self.schema_node, InternalNode)

    def put_member(self, name: InstanceName, value: Value,
                   raw: bool = False) -> "InstanceNode":
        """Return receiver's member with a new value.

        If the member is permitted by the schema but doesn't exist, it
        is created.

        Args:
            name: Instance name of the member.
            value: New value of the member.
            raw: Flag to be set if `value` is raw.

        Raises:
            NonexistentSchemaNode: If member `name` is not permitted by the
                schema.
            InstanceValueError: If the receiver's value is not an object.
        """
        if not isinstance(self.value, ObjectValue):
            raise InstanceValueError(self, "member of non-object")
        csn = self._member_schema_node(name)
        newval = self.value.copy()
        newval[name] = csn.from_raw(value) if raw else value
        return self._copy(newval)._member(name)

    def delete_item(self, key: InstanceKey) -> "InstanceNode":
        """Delete an item (member or entry) from receiver's value.

        Args:
            key: Key of the item (instance name or index).

        Raises:
            NonexistentInstance: If receiver's value doesn't contain the item.
            InstanceValueError: If the receiver's value is a scalar.
        """
        if not isinstance(self.value, StructuredValue):
            raise InstanceValueError(self, "scalar value")
        newval = self.value.copy()
        try:
            del newval[key]
        except (KeyError, IndexError, TypeError):
            raise NonexistentInstance(self, "item " + key) from None
        return self._copy(newval)

    def look_up(self, keys: Dict[InstanceName, ScalarValue]) -> "ArrayEntry":
        """Return the entry with matching keys.

        Args:
            keys: Dictionary of list keys and their values.

        Raises:
            InstanceValueError: If the receiver's value is not a YANG list.
            NonexistentInstance: If no entry with matching keys exists.
        """
        if not isinstance(self.value, ArrayValue):
            raise InstanceValueError(self, "lookup on non-list")
        try:
            for i in range(len(self.value)):
                en = self.value[i]
                flag = True
                for k in keys:
                    if en[k] != keys[k]:
                        flag = False
                        break
                if flag: return self._entry(i)
            raise NonexistentInstance(self, "entry lookup failed")
        except KeyError:
            raise NonexistentInstance(self, "entry lookup failed") from None
        except TypeError:
            raise InstanceValueError(self, "lookup on non-list") from None

    def up(self) -> "InstanceNode":
        """Return an instance node corresponding to the receiver's parent.

        Raises:
            NonexistentInstance: If there is no parent.
        """
        ts = max(self.timestamp, self.parinst.timestamp)
        return self.parinst._copy(self._zip(), ts)

    def top(self) -> "InstanceNode":
        """Return an instance node corresponding to the root of the data tree."""
        inst = self
        while inst.parinst:
            inst = inst.up()
        return inst

    def update(self, value: Union[RawValue, Value],
               raw: bool = False) -> "InstanceNode":
        """Update the receiver's value.

        Args:
            value: New value.
            raw: Flag to be set if `value` is raw.

        Returns:
            Copy of the receiver with the updated value.
        """
        newval = self.schema_node.from_raw(value) if raw else value
        return self._copy(newval)

    def goto(self, iroute: "InstanceRoute") -> "InstanceNode":
        """Move the focus to an instance inside the receiver's value.

        Args:
            iroute: Instance route (relative to the receiver).

        Returns:
            The instance node corresponding to the target instance.

        Raises:
            InstanceValueError: If `iroute` is incompatible with the receiver's
                value.
            NonexistentInstance: If the instance node doesn't exist.
        """
        inst = self
        for sel in iroute:
            inst = sel.goto_step(inst)
        return inst

    def peek(self, iroute: "InstanceRoute") -> Optional[Value]:
        """Return a value within the receiver's subtree.

        Args:
            iroute: Instance route (relative to the receiver).
        """
        val = self.value
        for sel in iroute:
            val = sel.peek_step(val)
            if val is None: return None
        return val

    def validate(self, scope: ValidationScope = ValidationScope.all,
                     ctype: ContentType = ContentType.config) -> None:
        """Validate the receiver's value.

        Args:
            scope: Scope of the validation (syntax, semantics or all).
            ctype: Receiver's content type.

        Raises:
            SchemaError: If the value doesn't conform to the schema.
            SemanticError: If the value violates a semantic constraint.
        """
        self.schema_node._validate(self, scope, ctype)

    def add_defaults(self, ctype: ContentType = None) -> "InstanceNode":
        """Return the receiver with defaults added recursively to its value.

        Args:
            ctype: Content type of the defaults to be added. If it is
                ``None``, the content type will be the same as receiver's.
        """
        sn = self.schema_node
        val = self.value
        if not (isinstance(val, ObjectValue) and isinstance(sn, InternalNode)):
            return self
        res = self
        if val:
            for mn in val:
                m = res._member(mn) if res is self else res.sibling(mn)
                res = m.add_defaults(ctype)
            res = res.up()
        return sn._add_defaults(res, ctype)

    def raw_value(self) -> RawValue:
        """Return receiver's value in a raw form (ready for JSON encoding)."""
        if isinstance(self.value, ObjectValue):
            return {m: self._member(m).raw_value() for m in self.value}
        if isinstance(self.value, ArrayValue):
            return [en.raw_value() for en in self]
        return self.schema_node.type.to_raw(self.value)

    def _member(self, name: InstanceName) -> "ObjectMember":
        sibs = self.value.copy()
        try:
            return ObjectMember(
                name, sibs, sibs.pop(name), self,
                self._member_schema_node(name), self.value.timestamp)
        except KeyError:
            raise NonexistentInstance(self, "member " + name) from None

    def _entry(self, index: int) -> "ArrayEntry":
        val = self.value
        i = len(val) + index if index < 0 else index
        try:
            return ArrayEntry(i, LinkedList.from_list(val[:i]),
                                  LinkedList.from_list(val[i+1:]),
                                  val[index], self, self.schema_node,
                                  val.timestamp)
        except (IndexError, TypeError):
            raise NonexistentInstance(self, "entry " + str(index)) from None

    def _peek_schema_route(self, sroute: SchemaRoute) -> Value:
        irt = InstanceRoute()
        sn = self.schema_node
        for qn in sroute:
            sn = sn.get_child(*qn)
            if sn is None:
                raise NonexistentSchemaNode(sn, *qn)
            if isinstance(sn, DataNode):
                irt.append(MemberName(sn.iname()))
        return self.peek(irt)

    def _member_schema_node(self, name: InstanceName) -> "DataNode":
        qname = self.schema_node._iname2qname(name)
        res = self.schema_node.get_data_child(*qname)
        if res is None:
            raise NonexistentSchemaNode(self.schema_node, *qname)
        return res

    def _node_set(self) -> List["InstanceNode"]:
        """XPath - return the list of all receiver's nodes."""
        return list(self) if isinstance(self.value, ArrayValue) else [self]

    def _children(self, qname:
                  Union[QualName, bool] = None) -> List["InstanceNode"]:
        """XPath - return the list of receiver's children."""
        sn = self.schema_node
        if not isinstance(sn, InternalNode): return []
        if qname:
            cn = sn.get_data_child(*qname)
            if cn is None: return []
            iname = cn.iname()
            if iname in self.value:
                return self._member(iname)._node_set()
            wd = cn._default_instance(self, ContentType.all, lazy=True)
            if iname not in wd.value: return []
            while True:
                cn = cn.parent
                if cn is sn:
                    return wd._member(iname)._node_set()
                if (cn.when and not cn.when.evaluate(self) or
                    isinstance(cn, CaseNode) and
                    cn.qual_name != cn.parent.default_case):
                    return []
        res = []
        wd = sn._add_defaults(self, ContentType.all, lazy=True)
        for mn in wd.value:
            res.extend(wd._member(mn)._node_set())
        return res

    def _descendants(self, qname: Union[QualName, bool] = None,
                    with_self: bool = False) -> List["InstanceNode"]:
        """XPath - return the list of receiver's descendants."""
        res = ([] if not with_self or (qname and self.qual_name != qname)
               else [self])
        for c in self._children():
            if not qname or c.qual_name == qname:
                res.append(c)
            res += c._descendants(qname)
        return res

    def _preceding_siblings(
            self, qname: Union[QualName, bool] = None) -> List["InstanceNode"]:
        """XPath - return the list of receiver's preceding-siblings."""
        return []

    def _following_siblings(
            self, qname: Union[QualName, bool] = None) -> List["InstanceNode"]:
        """XPath - return the list of receiver's following-siblings."""
        return []

    def _parent(self) -> List["InstanceNode"]:
        """XPath - return the receiver's parent as a singleton list."""
        return [self.up()]

    def _deref(self) -> List["InstanceNode"]:
        """XPath: return the list of nodes that the receiver refers to."""
        return ([] if self.is_internal() else
                self.schema_node.type._deref(self))

class RootNode(InstanceNode):
    """This class represents the root of the instance tree."""

    def __init__(self, value: Value, schema_node: "DataNode",
                 timestamp: datetime):
        self.path = ()
        self.parinst = None
        self.value = value
        self.schema_node = schema_node
        self.timestamp = timestamp

    def up(self) -> None:
        """Override the superclass method.

        Raises:
            NonexistentInstance: root node has no parent
        """
        raise NonexistentInstance(self, "up of top")

    def _copy(self, newval: Value, newts: datetime = None) -> InstanceNode:
        return RootNode(
            newval, self.schema_node, newts if newts else newval.timestamp)

    def _ancestors_or_self(
            self, qname: Union[QualName, bool] = None) -> List["RootNode"]:
        """XPath - return the list of receiver's ancestors including itself."""
        return [self] if qname is None else []

    def _ancestors(
            self, qname: Union[QualName, bool] = None) -> List["RootNode"]:
        """XPath - return the list of receiver's ancestors."""
        return []

class ObjectMember(InstanceNode):
    """This class represents an object member."""

    def __init__(self, key: InstanceName, siblings: Dict[InstanceName, Value],
                 value: Value, parinst: InstanceNode,
                 schema_node: "DataNode", timestamp: datetime ):
        super().__init__(key, value, parinst, schema_node, timestamp)
        self.siblings = siblings # type: Dict[InstanceName, Value]
        """Sibling members within the parent object."""

    @property
    def name(self) -> YangIdentifier:
        return self.path[-1]

    @property
    def qual_name(self) -> QualName:
        """Return the receiver's qualified name."""
        p, s, loc = self.name.partition(":")
        return (loc, p) if s else (p, self.namespace)

    def sibling(self, name: InstanceName) -> "ObjectMember":
        """Return an instance node corresponding to a sibling member.

        Args:
            name: Instance name of the sibling member.

        Raises:
            NonexistentSchemaNode: If member `name` is not permitted by the
                schema.
            NonexistentInstance: If sibling member `name` doesn't exist.
        """
        ssn = self.parinst._member_schema_node(name)
        try:
            sibs = self.siblings.copy()
            newval = sibs.pop(name)
            sibs[self.name] = self.value
            return ObjectMember(name, sibs, newval, self.parinst,
                                ssn, self.timestamp)
        except KeyError:
            raise NonexistentInstance(self, "member " + name) from None

    def _zip(self) -> ObjectValue:
        """Zip the receiver into an object and return it."""
        res = ObjectValue(self.siblings.copy(), self.timestamp)
        res[self.name] = self.value
        return res

    def _copy(self, newval: Value, newts: datetime = None) -> "ObjectMember":
        if newts:
            ts = newts
        elif isinstance(newval, StructuredValue):
            ts = newval.timestamp
        else:
            ts = datetime.now()
        return ObjectMember(self.name, self.siblings, newval, self.parinst,
                                self.schema_node, ts)

    def _ancestors_or_self(
            self, qname: Union[QualName, bool] = None) -> List[InstanceNode]:
        """XPath - return the list of receiver's ancestors including itself."""
        res = [] if qname and self.qual_name != qname else [self]
        return res + self.up()._ancestors_or_self(qname)

    def _ancestors(
            self, qname: Union[QualName, bool] = None) -> List[InstanceNode]:
        """XPath - return the list of receiver's ancestors."""
        return self.up()._ancestors_or_self(qname)

class ArrayEntry(InstanceNode):
    """This class represents an array entry."""

    def __init__(self, key: int, before: LinkedList, after: LinkedList,
                     value: Value, parinst: InstanceNode,
                     schema_node: "DataNode", timestamp: datetime = None):
        super().__init__(key, value, parinst, schema_node, timestamp)
        self.before = before # type: LinkedList
        """Preceding entries of the parent array."""
        self.after = after # type: LinkedList
        """Following entries of the parent array."""

    @property
    def index(self) -> int:
        """Index of the receiver in the parent array."""
        return self.path[-1]

    @property
    def name(self) -> InstanceName:
        """Name of the receiver."""
        return self.parinst.name

    @property
    def qual_name(self) -> Optional[QualName]:
        """Return the receiver's qualified name."""
        return self.parinst.qual_name

    def update(self, value: Union[RawValue, Value],
               raw: bool = False) -> "ArrayEntry":
        """Update the receiver's value.

        This method overrides the superclass method.
        """
        return super().update(self._cook_value(value, raw), False)

    def previous(self) -> "ArrayEntry":
        """Return an instance node corresponding to the previous entry.

        Raises:
            NonexistentInstance: If the receiver is the first entry of the
                parent array.
        """
        try:
            newval, nbef = self.before.pop()
        except IndexError:
            raise NonexistentInstance(self, "previous of first") from None
        return ArrayEntry(
            self.index - 1, nbef, self.after.cons(self.value), newval,
            self.parinst, self.schema_node, self.timestamp)

    def next(self) -> "ArrayEntry":
        """Return an instance node corresponding to the next entry.

        Raises:
            NonexistentInstance: If the receiver is the last entry of the parent array.
        """
        try:
            newval, naft = self.after.pop()
        except IndexError:
            raise NonexistentInstance(self, "next of last") from None
        return ArrayEntry(
            self.index + 1, self.before.cons(self.value), naft, newval,
            self.parinst, self.schema_node, self.timestamp)

    def insert_before(self, value: Union[RawValue, Value],
                      raw: bool = False) -> "ArrayEntry":
        """Insert a new entry before the receiver.

        Args:
            value: The value of the new entry.
            raw: Flag to be set if `value` is raw.

        Returns:
            An instance node of the new inserted entry.
        """
        return ArrayEntry(self.index, self.before, self.after.cons(self.value),
                              self._cook_value(value, raw), self.parinst,
                              self.schema_node, datetime.now())

    def insert_after(self, value: Union[RawValue, Value],
                     raw: bool = False) -> "ArrayEntry":
        """Insert a new entry after the receiver.

        Args:
            value: The value of the new entry.
            raw: Flag to be set if `value` is raw.

        Returns:
            An instance node of the newly inserted entry.
        """
        return ArrayEntry(self.index, self.before.cons(self.value), self.after,
                              self._cook_value(value, raw), self.parinst,
                              self.schema_node, datetime.now())

    def _cook_value(self, value: Union[RawValue, Value], raw: bool) -> Value:
        return (super(SequenceNode, self.schema_node).from_raw(value) if raw
                else value)

    def _zip(self) -> ArrayValue:
        """Zip the receiver into an array and return it."""
        res = list(self.before)
        res.reverse()
        res.append(self.value)
        res.extend(list(self.after))
        return ArrayValue(res, self.timestamp)

    def _copy(self, newval: Value, newts: datetime = None) -> "ArrayEntry":
        if newts:
            ts = newts
        elif isinstance(newval, StructuredValue):
            ts = newval.timestamp
        else:
            ts = datetime.now()
        return ArrayEntry(self.index, self.before, self.after, newval,
                              self.parinst, self.schema_node, ts)

    def _ancestors_or_self(
            self, qname: Union[QualName, bool] = None) -> List[InstanceNode]:
        """XPath - return the list of receiver's ancestors including itself."""
        res = [] if qname and self.qual_name != qname else [self]
        return res + self.up()._ancestors(qname)

    def _ancestors(
            self, qname: Union[QualName, bool] = None) -> List[InstanceNode]:
        """XPath - return the list of receiver's ancestors."""
        return self.up()._ancestors(qname)

    def _preceding_siblings(
            self, qname: Union[QualName, bool] = None) -> List[InstanceNode]:
        """XPath - return the list of receiver's preceding siblings."""
        if qname and self.qual_name != qname:
            return []
        res = []
        en = self
        for _ in self.before:
            en = en.previous()
            res.append(en)
        return res

    def _following_siblings(
            self, qname: Union[QualName, bool] = None) -> List[InstanceNode]:
        """XPath - return the list of receiver's following siblings."""
        if qname and self.qual_name != qname:
            return []
        res = []
        en = self
        for _ in self.after:
            en = en.next()
            res.append(en)
        return res

    def _parent(self) -> List["InstanceNode"]:
        """XPath - return the receiver's parent as a singleton list."""
        return [self.up().up()]

class InstanceRoute(list):
    """This class represents a route into an instance value."""

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "".join([str(c) for c in self])

    def __hash__(self) -> int:
        """Return the hash value of the receiver."""
        return self.__str__().__hash__()

class InstanceSelector:
    """Components of instance identifers."""

    def __init__(self, key: InstanceKey):
        """Initialize the class instance.

        Args:
            key: Member name or entry index.
        """
        self.key = key

    def __eq__(self, other: "InstanceSelector") -> bool:
        return self.key == other.key

    def peek_step(self, val: StructuredValue) -> Value:
        """Return the entry of `arr` addressed by the receiver.

        Args:
            val: Current value (object or array).
        """
        try:
            return val[self.key]
        except (IndexError, KeyError, TypeError):
            return None

    def goto_step(self, inst: InstanceNode) -> InstanceNode:
        """Return member instance of `inst` addressed by the receiver.

        Args:
            inst: Current instance.
        """
        return inst[self.key]

class MemberName(InstanceSelector):
    """Selectors of object members."""

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "/" + self.key

class EntryIndex(InstanceSelector):
    """Numeric selectors for a list or leaf-list entry."""

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "[{0:d}]".format(self.key)

class EntryValue(InstanceSelector):
    """Value-based selectors of an array entry."""

    def __init__(self, value: ScalarValue):
        """Initialize the class instance.

        Args:
            value: Value of a leaf-list entry.
        """
        self.value = value

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "[.=" + str(self.value) +"]"

    def __eq__(self, other: "EntryValue") -> bool:
        return self.value == other.value

    def peek_step(self, arr: ArrayValue) -> Value:
        """Return the entry of `arr` addressed by the receiver.

        Args:
            arr: Current array.
        """
        try:
            return arr[arr.index(self.value)]
        except ValueError:
            return None

    def goto_step(self, inst: InstanceNode) -> InstanceNode:
        """Return member instance of `inst` addressed by the receiver.

        Args:
            inst: Current instance.
        """
        try:
            return inst._entry(inst.value.index(self.value))
        except ValueError:
            raise NonexistentInstance(
                inst, "entry '{}'".format(str(self.value))) from None

class EntryKeys(InstanceSelector):
    """Key-based selectors for a list entry."""

    def __init__(self, keys: Dict[InstanceName, ScalarValue]):
        """Initialize the class instance.

        Args:
            keys: Dictionary with keys of an entry.
        """
        self.keys = keys

    def __str__(self) -> str:
        """Return a string representation of the receiver."""
        return "".join(["[{}={}]".format(k, repr(self.keys[k]))
                        for k in self.keys])

    def __eq__(self, other: "EntryKeys") -> bool:
        return self.keys == other.keys

    def peek_step(self, arr: ArrayValue) -> Value:
        """Return the entry of `arr` addressed by the receiver.

        Args:
            arr: Current array.
        """
        for en in arr:
            flag = True
            for k in self.keys:
                if en[k] != self.keys[k]:
                    flag = False
                    break
            if flag: return en

    def goto_step(self, inst: InstanceNode) -> InstanceNode:
        """Return member instance of `inst` addressed by the receiver.

        Args:
            inst: Current instance.
        """
        return inst.look_up(self.keys)

class InstancePathParser(Parser):
    """Abstract class for parsers of strings identifying instances."""

    def _member_name(self, sn: "InternalNode") -> Tuple[MemberName, "DataNode"]:
        """Parser object member name."""
        name, ns = self.prefixed_name()
        cn = sn.get_data_child(name, ns if ns else sn.ns)
        if cn is None:
            raise NonexistentSchemaNode(sn, name, ns)
        return (MemberName(cn.iname()), cn)

class ResourceIdParser(InstancePathParser):
    """Parser for RESTCONF resource identifiers."""

    def parse(self) -> InstanceRoute:
        """Parse resource identifier."""
        if self.peek() == "/": self.offset += 1
        res = InstanceRoute()
        sn = Context.schema
        while True:
            mnam, cn = self._member_name(sn)
            res.append(mnam)
            try:
                next = self.one_of("/=")
            except EndOfInput:
                return res
            if next == "=":
                res.append(self._key_values(cn))
                if self.at_end(): return res
            sn = cn

    def _key_values(self, sn: "SequenceNode") -> Union[EntryKeys, EntryValue]:
        """Parse leaf-list value or list keys."""
        try:
            keys = self.up_to("/")
        except EndOfInput:
            keys = self.remaining()
        if not keys:
            raise UnexpectedInput(self, "entry value or keys")
        if isinstance(sn, LeafListNode):
            return EntryValue(sn.type.parse_value(unquote(keys)))
        ks = keys.split(",")
        try:
            if len(ks) != len(sn.keys):
                raise UnexpectedInput(
                    self, "exactly {} keys".format(len(sn.keys)))
        except AttributeError:
            raise BadSchemaNodeType(sn, "list")
        sel = {}
        for j in range(len(ks)):
            knod = sn.get_data_child(*sn.keys[j])
            val = knod.type.parse_value(unquote(ks[j]))
            sel[knod.iname()] = val
        return EntryKeys(sel)

class InstanceIdParser(InstancePathParser):
    """Parser for YANG instance identifiers."""

    def parse(self) -> InstanceRoute:
        """Parse instance identifier."""
        res = InstanceRoute()
        sn = Context.schema
        while True:
            self.char("/")
            mnam, cn = self._member_name(sn)
            res.append(mnam)
            try:
                next = self.peek()
            except EndOfInput:
                return res
            if next == "[":
                self.offset += 1
                self.skip_ws()
                if self.peek() in "0123456789":
                    ind = self.unsigned_integer() - 1
                    if ind < 0:
                        raise UnexpectedInput(self, "positive index")
                    self.skip_ws()
                    self.char("]")
                    res.append(EntryIndex(ind))
                elif isinstance(cn, LeafListNode):
                    self.char(".")
                    res.append(EntryValue(self._get_value(cn)))
                else:
                    res.append(self._key_predicates(cn))
                if self.at_end(): return res
            sn = cn

    def _get_value(self, tn: "TerminalNode") -> ScalarValue:
        self.skip_ws()
        self.char("=")
        self.skip_ws()
        quote = self.one_of("'\"")
        val = self.up_to(quote)
        self.skip_ws()
        self.char("]")
        return tn.type.parse_value(val)

    def _key_predicates(self, sn: "ListNode") -> EntryKeys:
        "Parse one or more key predicates."""
        sel = {}
        while True:
            name, ns = self.prefixed_name()
            knod = sn.get_data_child(name, ns)
            val = self._get_value(knod)
            sel[knod.iname()] = val
            try:
                next = self.peek()
            except EndOfInput:
                break
            if next != "[": break
            self.offset += 1
            self.skip_ws()
        return EntryKeys(sel)

    def _key_values(self, sn: "SequenceNode") -> EntryKeys:
        """Parse leaf-list value or list keys."""
        try:
            keys = self.up_to("/")
        except EndOfInput:
            keys = self.remaining()
        if not keys:
            raise UnexpectedInput(self, "entry value or keys")
        if isinstance(sn, LeafListNode):
            return EntryValue(sn.type.parse_value(unquote(keys)))
        ks = keys.split(",")
        try:
            if len(ks) != len(sn.keys):
                raise UnexpectedInput(self,
                                      "exactly {} keys".format(len(sn.keys)))
        except AttributeError:
            raise BadSchemaNodeType(sn, "list")
        sel = {}
        for j in range(len(ks)):
            knod = sn.get_data_child(*sn.keys[j])
            val = knod.type.parse_value(unquote(ks[j]))
            sel[knod.iname()] = val
        return EntryKeys(sel)

class InstanceException(YangsonException):
    """Abstract class for exceptions related to operations on instance nodes."""

    def __init__(self, inst: InstanceNode):
        self.instance = inst

    def __str__(self):
        return "[" + self.instance.json_pointer() + "]"

class InstanceValueError(InstanceException):
    """The instance value is incompatible with the called method."""

    def __init__(self, inst: InstanceNode, detail: str):
        super().__init__(inst)
        self.detail = detail

    def __str__(self):
        return "{} {}".format(super().__str__(), self.detail)

class NonexistentInstance(InstanceException):
    """Attempt to access an instance node that doesn't exist."""

    def __init__(self, inst: InstanceNode, detail: str):
        super().__init__(inst)
        self.detail = detail

    def __str__(self):
        return "{} {}".format(super().__str__(), self.detail)

from .schema import (AnydataNode, CaseNode, ChoiceNode, DataNode, InternalNode,
                     LeafNode, LeafListNode, ListNode,
                     NonexistentSchemaNode, SequenceNode, TerminalNode)
