"""Classes for schema nodes."""

from typing import Dict, List, Optional, Tuple, Union
from .context import Context
from .datatype import DataType
from .enumerations import DefaultDeny
from .exception import YangsonException
from .instance import ArrayValue, EntryIndex, EntryValue, EntryKeys, ObjectValue
from .statement import Statement
from .typealiases import *
from .regex import *

# Type aliases
OptChangeSet = Optional["ChangeSet"]

class ChangeSet:
    """Set of changes to be applied to a node and its children."""

    @classmethod
    def from_statement(cls, stmt: Statement) -> "ChangeSet":
        """Construct an instance from a statement.

        :param stmt: YANG statement (``refine`` or ``uses-augment``)
        """
        path = stmt.argument.split("/")
        cs = cls([stmt])
        while path:
            last = path.pop()
            cs = cls(subset={last: cs})
        return cs

    def __init__(self, patch: List[Statement] = [],
                 subset: Dict[NodeName, "ChangeSet"] = {}) -> None:
        self.patch = patch
        self.subset = subset

    def get_subset(self, name: NodeName) -> OptChangeSet:
        return self.subset.get(name)

    def join(self, cs: "ChangeSet") -> "ChangeSet":
        """Join the receiver with another change set.

        :param cs: change set
        """
        res = ChangeSet(self.patch + cs.patch, self.subset.copy())
        for n in cs.subset:
            if n in self.subset:
                res.subset[n] = self.subset[n].join(cs.subset[n])
            else:
                res.subset[n] = cs.subset[n]
        return res

class SchemaNode:
    """Abstract superclass for schema nodes."""

    def __init__(self) -> None:
        """Initialize the class instance."""
        self.name = None # type: Optional[YangIdentifier]
        self.ns = None # type: Optional[YangIdentifier]
        self.parent = None # type: Optional["InternalNode"]
        self.default_deny = DefaultDeny.none # type: "DefaultDeny"

    @property
    def config(self) -> bool:
        """Is the receiver configuration?"""
        try:
            return self._config
        except AttributeError:
            return self.parent.config

    @property
    def qname(self) -> QName:
        """Return qualified name of the receiver."""
        return (self.name if self.ns == self.parent.ns
                else self.ns + ":" + self.name)

    def handle_substatements(self, stmt: Statement,
                             mid: ModuleId,
                             changes: Optional[ChangeSet]) -> None:
        """Dispatch actions for substatements of `stmt`.

        :param stmt: parsed YANG statement
        :param mid: YANG module context
        :param changes: change set
        """
        for s in stmt.substatements:
            if s.prefix:
                key = Context.prefix_map[mid][s.prefix][0] + ":" + s.keyword
            else:
                key = s.keyword
            mname = SchemaNode.handler.get(key, "noop")
            method = getattr(self, mname)
            method(s, mid, changes)

    def noop(self, stmt: Statement, mid: ModuleId,
             changes: OptChangeSet) -> None:
        """Do nothing."""
        pass

    def config_stmt(self, stmt: Statement,
                    mid: ModuleId,
                    changes: Optional[ChangeSet]) -> None:
        if stmt.argument == "false": self._config = False

    def nacm_default_deny_stmt(self, stmt: Statement,
                               mid: ModuleId,
                               changes: Optional[ChangeSet]) -> None:
        """Set NACM default access."""
        if stmt.keyword == "default-deny-all":
            self.default_deny = DefaultDeny.all
        elif stmt.keyword == "default-deny-write":
            self.default_deny = DefaultDeny.write

    handler = {
        "anydata": "anydata_stmt",
        "anyxml": "anyxml_stmt",
        "case": "case_stmt",
        "choice": "choice_stmt",
        "config": "config_stmt",
        "container": "container_stmt",
        "default": "default_stmt",
        "ietf-netconf-acm:default-deny-all": "nacm_default_deny_stmt",
        "ietf-netconf-acm:default-deny-write": "nacm_default_deny_stmt",
        "leaf": "leaf_stmt",
        "leaf-list": "leaf_list_stmt",
        "list": "list_stmt",
        "presence": "presence_stmt",
        "uses": "uses_stmt",
        }
    """Map of statement keywords to names of handler methods."""


class InternalNode(SchemaNode):
    """Abstract superclass for schema nodes that have children."""

    def __init__(self) -> None:
        """Initialize the class instance."""
        super().__init__()
        self.children = [] # type: List[SchemaNode]
        self._nsswitch = False # type: bool

    def add_child(self, node: SchemaNode) -> None:
        """Add child node to the receiver.

        :param node: child node
        """
        node.parent = self
        self.children.append(node)

    def get_child(
            self, name: YangIdentifier,
            ns: Optional[YangIdentifier] = None) -> Optional["SchemaNode"]:
        """Return receiver's child.
        :param name: child's name
        :param ns: child's namespace (= `self.ns` if absent)
        """
        ns = ns if ns else self.ns
        for c in self.children:
            if c.name == name and c.ns == ns: return c

    def get_schema_descendant(self,
                              path: SchemaAddress) -> Optional["SchemaNode"]:
        """Return descendant schema node or ``None``.

        :param path: schema address of the descendant node
        """
        node = self
        for ns, name in path:
            node = node.get_child(name, ns)
            if node is None:
                return None
        return node

    def get_data_child(
            self, name: YangIdentifier,
            ns: Optional[YangIdentifier] = None) -> Optional["DataNode"]:
        """Return data node directly under receiver.

        :param name: data node name
        :param ns: data node namespace (= `self.ns` if absent)
        """
        ns = ns if ns else self.ns
        cands = []
        for c in self.children:
            if c.name ==name and c.ns == ns:
                if isinstance(c, DataNode):
                    return c
                cands.insert(0,c)
            elif isinstance(c, (ChoiceNode, CaseNode)):
                cands.append(c)
        if cands:
            for c in cands:
                res = c.get_data_child(name, ns)
                if res: return res

    def handle_child(self, node: SchemaNode, stmt: Statement,
                     mid: ModuleId, changes: OptChangeSet) -> None:
        """Add child node to the receiver and handle substatements.

        :param node: child node
        :param stmt: YANG statement defining the child node
        :param mid: module context
        :param changes: change set
        """
        node.name = stmt.argument
        node.ns = mid[0] if self._nsswitch else self.ns
        self.add_child(node)
        node.handle_substatements(stmt, mid,
                                  changes.get_subset(name) if changes else None)

    def uses_stmt(self, stmt: Statement,
                  mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle uses statement."""
        grp, gid = Context.get_definition(stmt, mid)
        self.handle_substatements(grp, gid, changes)

    def container_stmt(self, stmt: Statement,
                  mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle container statement."""
        self.handle_child(ContainerNode(), stmt, mid, changes)

    def list_stmt(self, stmt: Statement,
                  mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle list statement."""
        self.handle_child(ListNode(), stmt, mid, changes)

    def choice_stmt(self, stmt: Statement,
                    mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle choice statement."""
        self.handle_child(ChoiceNode(), stmt, mid, changes)

    def case_stmt(self, stmt: Statement,
                  mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle case statement."""
        self.handle_child(CaseNode(), stmt, mid, changes)

    def leaf_stmt(self, stmt: Statement,
                  mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle leaf statement."""
        node = LeafNode()
        node.stmt_type(stmt, mid)
        self.handle_child(node, stmt, mid, changes)

    def leaf_list_stmt(self, stmt: Statement,
                       mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle leaf-list statement."""
        node = LeafListNode()
        node.stmt_type(stmt, mid)
        self.handle_child(node, stmt, mid, changes)

    def anydata_stmt(self, stmt: Statement,
                     mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle anydata statement."""
        self.handle_child(AnydataNode(), stmt, mid, changes)

    def anyxml_stmt(self, stmt: Statement,
                    mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle anyxml statement."""
        self.handle_child(AnyxmlNode(), stmt, mid, changes)

    def from_raw(self, val: Dict[QName, Value],
                 ns: YangIdentifier = None) -> ObjectValue:
        """Transform a raw dictionary into object value.

        :param val: raw dictionary
        :param ns: current namespace
        """
        res = ObjectValue()
        for qn in val:
            p, s, loc = qn.partition(":")
            if s:
                ns = p
            else:
                loc = p
            cn = self.get_data_child(loc, ns)
            res[cn.qname] = cn.from_raw(val[qn])
        res.time_stamp()
        return res

class DataNode:
    """Abstract superclass for data nodes."""

    def _parse_entry_selector(self, iid: str, offset: int) -> Any:
        """This method is applicable only to a list or leaf-list."""
        raise BadSchemaNodeType(self, "list or leaf-list")

    @property
    def type(self):
        """This method is applicable only to a terminal node."""
        raise BadSchemaNodeType(self, "leaf or leaf-list")

    @type.setter
    def type(self, typ: DataType) -> None:
        """This method is applicable only to a terminal node."""
        raise BadSchemaNodeType(self, "leaf or leaf-list")

class TerminalNode(SchemaNode, DataNode):
    """Abstract superclass for leaves in the schema tree."""

    def __init__(self) -> None:
        """Initialize the class instance."""
        super().__init__()
        self.default = None
        self._type = None # type: DataType

    @property
    def type(self) -> Optional[DataType]:
        """Return receiver's type."""
        return self._type

    @type.setter
    def type(self, typ: DataType) -> None:
        """Set receiver's type."""
        self._type = typ

    def stmt_type(self, stmt: Statement, mid: ModuleId) -> None:
        """Assign data type to the terminal node defined by `stmt`.

        :param stmt: YANG ``leaf`` or ``leaf-list`` statement
        :param mid: id of the context module
        """
        self.type = DataType.resolve_type(stmt.find1("type", required=True), mid)

    def from_raw(self, val: Value, ns: YangIdentifier = None) -> Value:
        """Transform a scalar entry.

        :param val: raw lis
        :param ns: current namespace
        """
        return val # TODO: handle special cases (Decimal64)

class ContainerNode(InternalNode, DataNode):
    """Container node."""

    def __init__(self) -> None:
        """Initialize the class instance."""
        super().__init__()
        self.presence = False # type: bool

    def presence_stmt(self, stmt: Statement, mid: ModuleId,
                      changes: OptChangeSet) -> None:
        self.presence = True


class ListNode(InternalNode, DataNode):
    """List node."""

    def _parse_entry_selector(self, iid: str, offset: int) -> Tuple[
            Union[EntryIndex, EntryKeys], int]:
        """Parse selector for a list entry.

        :param iid: instance identifier string
        :param offset:
        """
        res = {}
        key_expr = False
        while offset < len(iid) and iid[offset] == "[":
            mo = pred_re.match(iid, offset)
            if mo is None:
                raise BadInstanceIdentifier(iid)
            pos = mo.group("pos")
            if pos:
                if key_expr:
                    raise BadEntrySelector(self, iid)
                return (EntryIndex(int(pos) - 1), mo.end())
            key_expr = True
            name = mo.group("loc")
            ns = mo.group("prf")
            kn = self.get_data_child(name, ns)
            if kn is None:
                raise NonexistentSchemaNode(name, ns)
            drhs = mo.group("drhs")
            val = kn.type.parse_value(drhs if drhs else mo.group("srhs"))
            res[kn.qname] = val
            offset = mo.end()
        if res:
            return (EntryKeys(res), mo.end())
        raise BadEntrySelector(self, iid)

    def from_raw(self, val: List[Dict[QName, Value]],
                 ns: YangIdentifier = None) -> ArrayValue:
        """Transform a raw list array into array value.

        :param val: raw list array
        :param ns: current namespace
        """
        res = ArrayValue()
        for en in val:
            res.append(super().from_raw(en, ns))
        res.time_stamp()
        return res

class ChoiceNode(InternalNode):
    """Choice node."""

    def __init__(self) -> None:
        """Initialize the class instance."""
        super().__init__()
        self.default = None # type: NodeName

    def handle_child(self, node: SchemaNode, stmt: SchemaNode,
                     mid: ModuleId, changes: OptChangeSet) -> None:
        """Handle a child node to be added to the receiver.

        :param node: child node
        :param stmt: YANG statement defining the child node
        :param mid: module context
        :param changes: change set
        """
        if isinstance(node, CaseNode):
            super().handle_child(node, stmt, mid, changes)
        else:
            cn = CaseNode()
            cn.name = stmt.argument
            cn.ns = mid[0]
            self.add_child(cn)
            cn.handle_child(node, stmt, mid,
                            changes.get_subset(name) if changes else None)

    def default_stmt(self, stmt: Statement, mid: ModuleId,
                     changes: OptChangeSet) -> None:
        self.default = Context.translate_qname(mid, stmt.argument)

class CaseNode(InternalNode):
    """Case node."""
    pass

class LeafNode(TerminalNode):
    """Leaf node."""

    def default_stmt(self, stmt: Statement, mid: ModuleId,
                     changes: OptChangeSet) -> None:
        self.default = self.type.parse_value(stmt.argument)

class LeafListNode(TerminalNode):
    """Leaf-list node."""

    def default_stmt(self, stmt: Statement, mid: ModuleId,
                     changes: OptChangeSet) -> None:
        if self.default is None:
            self.default = []
        self.default.append(self.type.parse_value(stmt.argument))

    def _parse_entry_selector(self, iid: str, offset: int) -> Tuple[
            Union[EntryIndex, EntryValue], int]:
        """Parse selector for a leaf-list entry.

        :param iid: instance identifier string
        :param offset:
        """
        if iid[offset] != "[":
            raise BadEntrySelector(self, iid)
        mo = pred_re.match(iid, offset)
        if mo is None:
            raise BadEntrySelector(self, iid)
        pos = mo.group("pos")
        if pos:
            return (EntryIndex(int(pos) - 1), mo.end())
        else:
            if mo.group("loc"):
                raise BadEntrySelector(self, iid)
            drhs = mo.group("drhs")
            val = self.type.parse_value(drhs if drhs else mo.group("srhs"))
            return (EntryValue(val), mo.end())

    def from_raw(self, val: List[Dict[QName, Value]],
                 ns: YangIdentifier = None) -> ArrayValue:
        """Transform a raw list array into array value.

        :param val: raw list array
        :param ns: current namespace
        """
        res = ArrayValue()
        for en in val:
            res.append(super().from_raw(en, ns))
        res.time_stamp()
        return res

class AnydataNode(TerminalNode):
    """Anydata node."""
    pass

class AnyxmlNode(TerminalNode):
    """Anyxml node."""
    pass

class NonexistentSchemaNode(YangsonException):
    """Exception to be raised when a schema node doesn't exist."""

    def __init__(self, name: YangIdentifier,
                 ns: Optional[YangIdentifier]) -> None:
        self.qname = (ns + ":" if ns else "") + name

    def __str__(self) -> str:
        return self.qname

class SchemaNodeError(YangsonException):
    """Abstract exception class for schema node errors."""

    def __init__(self, sn: SchemaNode) -> None:
        self.schema_node = sn

    def __str__(self) -> str:
        return self.schema_node.qname

class BadSchemaNodeType(SchemaNodeError):
    """Exception to be raised when a schema node is of a wrong type."""

    def __init__(self, sn: SchemaNode, expected: str) -> None:
        super().__init__(sn)
        self.expected = expected

    def __str__(self) -> str:
        return super().__str__() + " is not a " + self.expected

class BadEntrySelector(SchemaNodeError):
    """Exception to be raised when a schema node is of a wrong type."""

    def __init__(self, sn: SchemaNode, iid: str) -> None:
        super().__init__(sn)
        self.iid = iid

    def __str__(self) -> str:
        return "in '" + self.iid + "' for " + super().__str__()