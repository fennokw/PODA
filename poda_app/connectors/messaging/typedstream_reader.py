# Adapted from imessage-chatdb (MIT), commit 3fe1561f1561e7b5c2c2076ac75368c896f5ca93. See TYPEDSTREAM_LICENSE.txt.
"""Full typedstream reader for ``message.attributedBody`` (docs/TYPEDSTREAM.md section 9).

:mod:`imessage_chatdb.typedstream` keeps the 0.1 byte-scan (``extract_text``)
untouched; this module reads the whole archive: the text, every attribute run
with its UTF-16 offsets and attribute dictionary, and a ``message_parts`` view
that splits the text into attachment placeholders and text parts with their
mentions and links.

Two layers:

1. ``_Reader`` is the generic grammar of docs/TYPEDSTREAM.md sections 1 to 4:
   the header, typedstream integers, the two shared tables (strings; objects,
   classes and C strings), typed-value groups and literal objects.  Every
   loop consumes at least one byte or raises, references are table lookups
   that never re-parse, and both object nesting and class-chain length are
   capped (``_MAX_DEPTH``) by counting, never by recursion depth, so parsing
   is linear in the input, cannot hang and cannot overflow the interpreter
   stack.  Any defect raises :class:`TypedStreamError` with the byte offset.
2. :func:`parse_attributed_body` interprets the parsed object tree as an
   ``NSAttributedString`` (sections 5 and 6): the string field, the
   ``(dictionary index, run length)`` pairs with their index rule, and the
   attribute value classes (``NSString``, ``NSNumber``, ``NSURL``, ``NSData``,
   nested ``NSDictionary``).  Any other value class, or a known class whose
   fields are not laid out as expected, is kept as :class:`UnknownValue`
   after its fields were parsed structurally, so an unexpected object inside
   one attribute never costs the text.  Every archived object is converted
   once (the result is cached on the object, so a string or dictionary that
   twenty runs reference is decoded once and shared), and the nesting of
   converted values is capped at ``_MAX_DEPTH`` mappings even when the
   nesting goes through back-references rather than literals.

Nothing here opens a database or decodes keyed archives; no runtime
dependency.  Only ``try_parse_attributed_body`` swallows errors.
"""

from __future__ import annotations

import base64
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

__all__ = [
    "TypedStreamError",
    "Url",
    "UnknownValue",
    "AttributeValue",
    "AttributeRun",
    "AttributedBody",
    "parse_attributed_body",
    "try_parse_attributed_body",
    "Mention",
    "TextPart",
    "AttachmentPart",
    "UnknownPart",
    "Part",
    "message_parts",
    "PART_KEY",
    "FILE_TRANSFER_GUID_KEY",
    "MENTION_KEY",
    "LINK_KEY",
]

#: ``__kIMMessagePartAttributeName``: the 0-based part index of a run.
PART_KEY = "__kIMMessagePartAttributeName"
#: ``__kIMFileTransferGUIDAttributeName``: the attachment guid of a U+FFFC run.
FILE_TRANSFER_GUID_KEY = "__kIMFileTransferGUIDAttributeName"
#: ``__kIMMentionConfirmedMention``: the mentioned handle; the run text is the display name.
MENTION_KEY = "__kIMMentionConfirmedMention"
#: ``__kIMLinkAttributeName``: an ``NSURL`` (:class:`Url`).
LINK_KEY = "__kIMLinkAttributeName"

#: Attribute keys that carry no text meaning: a run whose attributes are only
#: these (and no part index) is an :class:`UnknownPart` (the live breadcrumb runs).
_NON_TEXT_KEYS = frozenset(
    {"__kIMBreadcrumbTextMarkerAttributeName", "__kIMBreadcrumbTextOptionFlags"}
)

_PLACEHOLDER = "￼"
_SIGNATURE = b"streamtyped"
_STREAMER_VERSION = 4
_SYSTEM_VERSION = 1000
_REFERENCE_BASE = 110  # reference r stands for table index r + 110 (0x92 -> 0)
# One cap for the three things a crafted blob could otherwise grow without bound:
# literal object nesting (observed maximum 10), the length of one class chain
# (observed maximum 3) and the nesting of converted attribute mappings, which
# back-references can deepen past the literal nesting (observed maximum 2).
_MAX_DEPTH = 64
_MAX_ARRAY_DIGITS = 9  # ``[Nc]``: N wider than this cannot fit in any real blob
# Only ASCII digits count in ``[Nc]``: ``str.isdigit`` also accepts superscripts and
# other Unicode digits that reach the latin-1-decoded type string from a corrupt
# byte, and ``int()`` then raises ``ValueError`` instead of ``TypedStreamError``.
_ASCII_DIGITS = frozenset("0123456789")

_TAG_INT16 = 0x81
_TAG_INT32 = 0x82
_TAG_FLOAT = 0x83
_TAG_NEW = 0x84
_TAG_NIL = 0x85
_TAG_END = 0x86
_TAG_LOW = 0x80
_TAG_HIGH = 0x91

_ROOT_CLASSES = ("NSAttributedString", "NSMutableAttributedString")
_STRING_CLASSES = ("NSString", "NSMutableString")
_DATA_CLASSES = ("NSData", "NSMutableData")

_SIGNED_TYPES = "islq"
_UNSIGNED_TYPES = "ISLQ"
_BYTE_TYPES = "cCB"


class TypedStreamError(ValueError):
    """Malformed typedstream.  The message says the byte offset and what was expected."""


@dataclass(frozen=True, slots=True)
class Url:
    """An ``NSURL`` attribute value: the archived relative string (the base is always nil)."""

    relative: str


@dataclass(frozen=True, slots=True)
class UnknownValue:
    """An attribute value the reader does not interpret.

    Either a class it does not know (``NSArray``, ``NSMutableDictionary``,
    ...) or a known class whose fields are not laid out as the reader expects
    (an ``NSString`` without its ``+`` field, an ``NSDictionary`` whose pair
    count does not match, an ``NSURL`` without a string, ...).  Its fields
    were parsed structurally and discarded; only the most derived class name
    is kept.
    """

    class_name: str


AttributeValue = Any  # Recursive type alias adapted for Python 3.11.


def _utf16_length(text: str) -> int:
    """Number of UTF-16 code units in ``text`` (an astral code point is two).

    C-speed: one encode.  A lone surrogate (never produced by the parser,
    which decodes with ``errors="replace"``) counts as one unit.
    """
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _code_point_index(text: str, unit: int) -> int:
    """Index of the code point containing UTF-16 unit ``unit`` (``len(text)`` past the end).

    A unit that falls on the second half of a surrogate pair maps to the pair's
    index, which is how ``AttributeRun.chars`` rounds a boundary down.
    """
    if unit <= 0:
        return 0
    units = 0
    for i, ch in enumerate(text):
        width = 2 if ord(ch) > 0xFFFF else 1
        if units + width > unit:
            return i
        units += width
    return len(text)


@dataclass(frozen=True, slots=True)
class AttributeRun:
    """One attribute run: ``[start, end)`` in UTF-16 code units of ``AttributedBody.text``.

    ``attributes`` is read-only (a ``MappingProxyType``); runs that reuse one
    archived dictionary share the same mapping object.
    """

    start: int
    end: int
    attributes: Mapping[str, AttributeValue]

    @property
    def length(self) -> int:
        """The archived run length in UTF-16 code units."""
        return self.end - self.start

    def chars(self, text: str) -> str:
        """The run's slice of ``text`` in Python code points.

        Offsets are converted from UTF-16 units; a boundary that falls inside
        a surrogate pair (never on a well-formed archive) is rounded down to
        the code point containing it, so the runs' slices still cover ``text``
        exactly once between them.  Text without astral code points (one unit
        per code point) is sliced directly.
        """
        if len(text) == _utf16_length(text):
            return text[max(self.start, 0) : max(self.end, 0)]
        return text[_code_point_index(text, self.start) : _code_point_index(text, self.end)]


def _run_texts(text: str, runs: tuple[AttributeRun, ...]) -> list[str]:
    """``[run.chars(text) for run in runs]`` in one pass over ``text``.

    The unit -> code-point table is built once per body, so a body with
    thousands of runs costs ``O(len(text) + len(runs))`` rather than one walk
    of the text per run.
    """
    units = _utf16_length(text)
    if len(text) == units:
        return [text[max(r.start, 0) : max(r.end, 0)] for r in runs]
    index_at_unit: list[int] = []
    for i, ch in enumerate(text):
        index_at_unit.append(i)
        if ord(ch) > 0xFFFF:
            index_at_unit.append(i)  # the pair's second unit rounds down to the pair
    index_at_unit.append(len(text))
    out: list[str] = []
    for r in runs:
        start = min(max(r.start, 0), units)
        end = min(max(r.end, 0), units)
        out.append(text[index_at_unit[start] : index_at_unit[end]])
    return out


@dataclass(frozen=True, slots=True)
class AttributedBody:
    """A parsed ``attributedBody``: the text and its attribute runs, in order.

    Runs are contiguous: ``runs[0].start == 0``, each run starts where the
    previous ended, and ``runs[-1].end`` is the UTF-16 length of ``text``.
    ``runs`` is empty only for an archive with empty text and no run at all
    (Apple's archiver always writes at least one run).  ``root_class`` is
    ``"NSAttributedString"`` or ``"NSMutableAttributedString"``.
    """

    text: str
    runs: tuple[AttributeRun, ...]
    root_class: str

    @property
    def mutable(self) -> bool:
        """True when the root is ``NSMutableAttributedString``."""
        return self.root_class == "NSMutableAttributedString"


# ---------------------------------------------------------------------------
# generic grammar
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Class:
    name: str
    version: int
    superclass: _Class | None
    #: The chain's names, most derived first; computed once per class (a chain
    #: is at most ``_MAX_DEPTH`` long, so this is bounded work per class).
    chain: tuple[str, ...]


@dataclass(slots=True)
class _Object:
    """A literal object: its class chain (most derived first) and its typed-value groups.

    ``cache`` holds the object's converted attribute value and the nesting
    height of that value (0 for a scalar, 1 + the deepest value for a mapping)
    once ``_Converter`` has produced it, so an object referenced from many
    places is converted once and shared.
    """

    chain: tuple[str, ...]
    groups: list[tuple[str, tuple[object, ...]]]
    cache: tuple[AttributeValue, int] | None = None


# An object-table entry: an object, a class, a C string, or ``None`` while the
# entry's literal is still being read (a reference to it is a cycle: an error).
_Entry = _Object | _Class | bytes | None


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.strings: list[bytes] = []
        self.objects: list[_Entry] = []
        self.depth = 0

    # ----- errors and raw bytes

    def fail(self, what: str) -> TypedStreamError:
        return TypedStreamError(f"offset {self.pos}: {what}")

    def byte(self) -> int:
        if self.pos >= len(self.data):
            raise self.fail("unexpected end of data")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def peek(self) -> int:
        if self.pos >= len(self.data):
            raise self.fail("unexpected end of data")
        return self.data[self.pos]

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise self.fail(f"{n} bytes requested, {len(self.data) - self.pos} left")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    # ----- integers

    def integer(self, *, signed: bool) -> int:
        """A typedstream integer (section 2.2).  ``0x83`` and reserved tags raise."""
        head = self.byte()
        if head < _TAG_LOW or head > _TAG_HIGH:
            if signed:
                return head - 0x100 if head >= 0x80 else head
            return head
        if head == _TAG_INT16:
            return int.from_bytes(self.take(2), "little", signed=signed)
        if head == _TAG_INT32:
            return int.from_bytes(self.take(4), "little", signed=signed)
        if head == _TAG_FLOAT:
            raise self.fail("float tag 0x83 in an integer context")
        raise self.fail(f"tag {head:#04x} where an integer was expected")

    def floating(self, fmt: str, width: int) -> float:
        head = self.peek()
        if head == _TAG_FLOAT:
            self.pos += 1
            value: float = struct.unpack(fmt, self.take(width))[0]
            return value
        return float(self.integer(signed=True))

    def reference(self, table: list[Any], what: str) -> int:
        """A reference head (already known not to be ``0x84``/``0x85``) -> table index."""
        at = self.pos
        index = self.integer(signed=True) + _REFERENCE_BASE
        if not 0 <= index < len(table):
            raise TypedStreamError(
                f"offset {at}: {what} reference {index} out of range (table has {len(table)})"
            )
        return index

    # ----- strings

    def unshared_string(self) -> bytes | None:
        """``0x85`` nil, else an unsigned length and that many bytes (section 2.4)."""
        if self.peek() == _TAG_NIL:
            self.pos += 1
            return None
        return self.take(self.integer(signed=False))

    def shared_string(self) -> bytes | None:
        """Type strings, class names, C-string bodies: nil, literal (appended) or reference."""
        head = self.peek()
        if head == _TAG_NIL:
            self.pos += 1
            return None
        if head == _TAG_NEW:
            self.pos += 1
            value = self.unshared_string()
            if value is None:
                raise self.fail("nil body after 0x84 in a shared string")
            self.strings.append(value)
            return value
        return self.strings[self.reference(self.strings, "string")]

    def c_string(self) -> bytes | None:
        """A ``*`` value: nil, ``0x84`` + shared string (takes an O slot), or an O reference."""
        head = self.peek()
        if head == _TAG_NIL:
            self.pos += 1
            return None
        if head == _TAG_NEW:
            self.pos += 1
            slot = len(self.objects)
            self.objects.append(None)
            value = self.shared_string()
            if value is None:
                raise self.fail("nil body in a literal C string")
            self.objects[slot] = value
            return value
        found: bytes = self.object_entry(bytes, "C string")
        return found

    def object_entry(self, kind: type, what: str) -> Any:
        at = self.pos
        entry = self.objects[self.reference(self.objects, what)]
        if entry is None:
            raise TypedStreamError(f"offset {at}: {what} reference to an entry still being read")
        if not isinstance(entry, kind):
            raise TypedStreamError(
                f"offset {at}: {what} reference names a {type(entry).__name__.lstrip('_')}"
            )
        return entry

    # ----- classes and objects

    def class_chain(self) -> _Class | None:
        """``0x85`` nil (root reached), ``0x84`` literal class (takes a slot), or an O reference.

        A literal class is followed by its superclass in the same three forms,
        so a chain is a run of literals ended by nil or by a reference.  It is
        read iteratively (a chain of thousands of literal classes must raise
        :class:`TypedStreamError`, never ``RecursionError``) and capped at
        ``_MAX_DEPTH`` classes; each literal takes its object-table slot in
        stream order, before its superclass, exactly as the recursive reading
        would.
        """
        pending: list[tuple[int, str, int]] = []  # (slot, name, version), outermost first
        chain: _Class | None
        while True:
            head = self.peek()
            if head == _TAG_NIL:
                self.pos += 1
                chain = None
                break
            if head != _TAG_NEW:
                chain = self.object_entry(_Class, "class")
                break
            if len(pending) >= _MAX_DEPTH:
                raise self.fail(f"class chain longer than {_MAX_DEPTH}")
            self.pos += 1
            slot = len(self.objects)
            self.objects.append(None)
            name = self.shared_string()
            if name is None:
                raise self.fail("nil class name")
            version = self.integer(signed=True)
            pending.append((slot, name.decode("utf-8", errors="replace"), version))
        inherited = len(chain.chain) if chain is not None else 0
        if inherited + len(pending) > _MAX_DEPTH:
            raise self.fail(f"class chain longer than {_MAX_DEPTH}")
        for slot, class_name, version in reversed(pending):
            names = (class_name, *chain.chain) if chain is not None else (class_name,)
            chain = _Class(class_name, version, chain, names)
            self.objects[slot] = chain
        return chain

    def read_object(self) -> _Object | None:
        """``0x85`` nil, ``0x84`` literal object (O slot taken before its class), or a reference."""
        head = self.peek()
        if head == _TAG_NIL:
            self.pos += 1
            return None
        if head == _TAG_NEW:
            if self.depth >= _MAX_DEPTH:
                raise self.fail(f"object nesting deeper than {_MAX_DEPTH}")
            self.pos += 1
            slot = len(self.objects)
            self.objects.append(None)
            cls = self.class_chain()
            if cls is None:
                raise self.fail("object without a class")
            obj = _Object(cls.chain, [])
            self.depth += 1
            while self.peek() != _TAG_END:
                obj.groups.append(self.group())
            self.pos += 1
            self.depth -= 1
            self.objects[slot] = obj
            return obj
        found: _Object = self.object_entry(_Object, "object")
        return found

    # ----- typed-value groups

    def group(self) -> tuple[str, tuple[object, ...]]:
        """A type string (shared) followed by one value per type character."""
        at = self.pos
        raw = self.shared_string()
        if raw is None:
            raise TypedStreamError(f"offset {at}: nil type string")
        type_string = raw.decode("latin-1")
        values: list[object] = []
        i = 0
        n = len(type_string)
        while i < n:
            ch = type_string[i]
            i += 1
            if ch == "[":
                j = i
                while j < n and type_string[j] in _ASCII_DIGITS:
                    j += 1
                if j == i or j - i > _MAX_ARRAY_DIGITS:
                    raise self.fail(f"bad array type {type_string!r}")
                count = int(type_string[i:j])
                if j + 1 >= n or type_string[j + 1] != "]":
                    raise self.fail(f"bad array type {type_string!r}")
                if type_string[j] not in _BYTE_TYPES:
                    raise self.fail(f"array of non-byte elements {type_string!r} is not supported")
                i = j + 2
                values.append(self.take(count))
            else:
                values.append(self.scalar(ch, type_string))
        return type_string, tuple(values)

    def scalar(self, ch: str, type_string: str) -> object:
        if ch == "@":
            return self.read_object()
        if ch == "+":
            return self.unshared_string()
        if ch == "*":
            return self.c_string()
        if ch in _BYTE_TYPES:
            return self.byte()
        if ch in _SIGNED_TYPES:
            return self.integer(signed=True)
        if ch in _UNSIGNED_TYPES:
            return self.integer(signed=False)
        if ch == "f":
            return self.floating("<f", 4)
        if ch == "d":
            return self.floating("<d", 8)
        if ch == "#":
            return self.class_chain()
        if ch in "%:":
            return self.shared_string()
        raise self.fail(f"unsupported type character {ch!r} in {type_string!r}")

    # ----- header and top level

    def header(self) -> None:
        if self.byte() != _STREAMER_VERSION:
            self.pos -= 1
            raise self.fail("not a typedstream (streamer version is not 4)")
        signature = self.unshared_string()
        if signature != _SIGNATURE:
            raise self.fail("not a little-endian 'streamtyped' archive")
        if self.integer(signed=False) != _SYSTEM_VERSION:
            raise self.fail("unexpected system version (expected 1000)")

    def finish(self) -> None:
        if self.pos != len(self.data):
            raise self.fail(f"{len(self.data) - self.pos} trailing bytes after the root object")


# ---------------------------------------------------------------------------
# NSAttributedString interpretation
# ---------------------------------------------------------------------------


class _NestingError(TypedStreamError):
    """Converted values nested deeper than ``_MAX_DEPTH`` mappings.

    The one conversion error that is never wrapped as :class:`UnknownValue`:
    it is the guard against a reference graph that would otherwise build a
    result no consumer (``to_dict``, ``json.dumps``) could walk.
    """


def _describe(value: object) -> str:
    if value is None:
        return "nil"
    if isinstance(value, _Object):
        return value.chain[0]
    return type(value).__name__


def _number_of(obj: _Object) -> AttributeValue:
    """``NSNumber``: group ``*`` (the Objective-C type) then one value group -> ``int``."""
    if len(obj.groups) == 2 and obj.groups[0][0] == "*" and len(obj.groups[1][1]) == 1:
        value = obj.groups[1][1][0]
        if isinstance(value, int):
            return value
    return UnknownValue(obj.chain[0])


def _data_of(obj: _Object) -> AttributeValue:
    """``NSData``/``NSMutableData``: group ``i`` (length) then group ``[Nc]`` (the bytes)."""
    if len(obj.groups) == 2 and obj.groups[0][0] == "i" and obj.groups[1][0].startswith("["):
        payload = obj.groups[1][1][0]
        if isinstance(payload, bytes):
            return payload
    return UnknownValue(obj.chain[0])


class _Converter:
    """Parsed objects -> attribute values, each object converted once.

    The result (and, for a mapping, its nesting height) is cached on the
    ``_Object``, so an object that many runs or pairs reference is decoded
    once and shared; the work is linear in the number of archived objects.

    ``depth`` is the number of mappings enclosing the value being converted
    (the run dictionary itself is at depth 0).  A mapping is entered only
    while ``depth < _MAX_DEPTH`` and a cached mapping of height ``h`` is
    embedded only where ``depth + h <= _MAX_DEPTH``, so no converted value
    nests more than ``_MAX_DEPTH`` mappings however the archive's
    back-references are arranged (literal nesting is capped by ``_Reader``;
    references can nest deeper than literals, and did in a crafted blob).

    :meth:`string` and :meth:`dictionary` are strict (the text, a key and a
    run dictionary must be what they claim).  :meth:`value` is lenient: in
    value position a known class with an unexpected layout becomes
    :class:`UnknownValue`, as an unknown class does, so one odd attribute
    never costs the text.  Only :class:`_NestingError` passes through.
    """

    __slots__ = ()

    def string(self, value: object) -> str:
        """The text of an ``NSString``/``NSMutableString`` object; anything else raises."""
        if not isinstance(value, _Object) or not any(c in _STRING_CLASSES for c in value.chain):
            raise TypedStreamError(f"expected an NSString object, got {_describe(value)}")
        if value.cache is not None and isinstance(value.cache[0], str):
            return value.cache[0]
        if len(value.groups) != 1 or value.groups[0][0] != "+":
            raise TypedStreamError("NSString without exactly one '+' field")
        raw = value.groups[0][1][0]
        if not isinstance(raw, bytes):
            raise TypedStreamError("NSString with a nil string")
        text = raw.decode("utf-8", errors="replace")
        value.cache = (text, 0)
        return text

    def dictionary(self, obj: object, depth: int = 0) -> Mapping[str, AttributeValue]:
        """``NSDictionary``: group ``i`` = pair count, then ``@`` key / ``@`` value per pair.

        Pairs are a mapping (enumeration order is not meaningful; a duplicate
        key keeps the last value).  A key that is not an ``NSString``, a pair
        count that does not match the fields, or a pair that is not two
        objects raises.
        """
        return self._dictionary(obj, depth)[0]

    def value(self, value: object, depth: int = 1) -> AttributeValue:
        """The attribute value of ``value`` (an object or nil) inside ``depth`` mappings."""
        return self._convert(value, depth)[0]

    def _dictionary(self, obj: object, depth: int) -> tuple[Mapping[str, AttributeValue], int]:
        if not isinstance(obj, _Object) or "NSDictionary" not in obj.chain:
            raise TypedStreamError(f"expected an NSDictionary, got {_describe(obj)}")
        if obj.cache is not None:
            cached, height = obj.cache
            if isinstance(cached, Mapping):
                if depth + height > _MAX_DEPTH:
                    raise _NestingError(
                        f"attribute values nested deeper than {_MAX_DEPTH} mappings"
                    )
                return cached, height
        if depth >= _MAX_DEPTH:
            raise _NestingError(f"attribute values nested deeper than {_MAX_DEPTH} mappings")
        groups = obj.groups
        if not groups or groups[0][0] != "i":
            raise TypedStreamError("NSDictionary without its pair count")
        count = groups[0][1][0]
        if not isinstance(count, int) or count < 0 or len(groups) != 1 + 2 * count:
            raise TypedStreamError(f"NSDictionary pair count {count!r} does not match its fields")
        out: dict[str, AttributeValue] = {}
        height = 1
        for k in range(count):
            key_group, value_group = groups[1 + 2 * k], groups[2 + 2 * k]
            if key_group[0] != "@" or value_group[0] != "@":
                raise TypedStreamError("NSDictionary pair is not two objects")
            key = self.string(key_group[1][0])
            out[key], value_height = self._convert(value_group[1][0], depth + 1)
            height = max(height, value_height + 1)
        mapping: Mapping[str, AttributeValue] = MappingProxyType(out)
        obj.cache = (mapping, height)
        return mapping, height

    def _convert(self, value: object, depth: int) -> tuple[AttributeValue, int]:
        if value is None:
            return None, 0
        if not isinstance(value, _Object):  # pragma: no cover - '@' values are objects or nil
            raise TypedStreamError(f"attribute value is not an object: {_describe(value)}")
        if value.cache is not None:
            converted, height = value.cache
            if depth + height > _MAX_DEPTH:
                raise _NestingError(f"attribute values nested deeper than {_MAX_DEPTH} mappings")
            return converted, height
        name = value.chain[0]
        result: tuple[AttributeValue, int]
        try:
            if name in _STRING_CLASSES:
                result = (self.string(value), 0)
            elif name == "NSNumber":
                result = (_number_of(value), 0)
            elif name == "NSURL":
                result = (self._url(value), 0)
            elif name in _DATA_CLASSES:
                result = (_data_of(value), 0)
            elif name == "NSDictionary":
                result = self._dictionary(value, depth)
            else:
                result = (UnknownValue(name), 0)
        except _NestingError:
            raise
        except TypedStreamError:
            # A known class with an unexpected layout (an NSString without its
            # '+' field, a nested NSDictionary whose count or keys are off, an
            # NSURL whose string is broken): the fields were parsed
            # structurally by the grammar, so keep the attribute as unknown.
            result = (UnknownValue(name), 0)
        value.cache = result
        return result

    def _url(self, obj: _Object) -> AttributeValue:
        """``NSURL``: group ``c`` (flag byte, always 0) then group ``@`` with the string."""
        if len(obj.groups) == 2 and obj.groups[0][0] == "c" and obj.groups[1][0] == "@":
            target = obj.groups[1][1][0]
            if isinstance(target, _Object) and any(c in _STRING_CLASSES for c in target.chain):
                return Url(self.string(target))
        return UnknownValue(obj.chain[0])


def parse_attributed_body(data: bytes) -> AttributedBody:
    """Parse an ``attributedBody`` blob; raise :class:`TypedStreamError` on any defect.

    The rules of docs/TYPEDSTREAM.md section 9.1: the exact header, one
    top-level ``@`` group holding an ``NSAttributedString`` or
    ``NSMutableAttributedString`` with nothing after it, a first field that is
    an ``NSString`` (``text``, UTF-8 with ``errors="replace"``), then
    ``(dictionary index, run length)`` pairs whose index rule is enforced and
    whose lengths sum to the UTF-16 length of ``text``.  Attribute values are
    converted (``NSString`` -> ``str``, ``NSNumber`` -> ``int``, ``NSURL`` ->
    :class:`Url`, ``NSData`` -> ``bytes``, ``NSDictionary`` -> mapping, nil ->
    ``None``); any other class, or a known class with an unexpected layout,
    becomes :class:`UnknownValue` (the text string, a dictionary key and a
    run dictionary must be well formed: those raise).  Each archived object
    is converted once however many runs or pairs reference it, and converted
    values nest at most ``_MAX_DEPTH`` mappings deep (deeper raises).  A
    truncated blob always raises: the text is never partial.  ``bytearray``
    and ``memoryview`` input is accepted.
    """
    reader = _Reader(bytes(data))
    reader.header()
    type_string, values = reader.group()
    if type_string != "@":
        raise TypedStreamError(f"root group has type {type_string!r}, expected '@'")
    reader.finish()
    root = values[0]
    if not isinstance(root, _Object) or root.chain[0] not in _ROOT_CLASSES:
        raise TypedStreamError(f"root object is {_describe(root)}, expected an NSAttributedString")
    groups = root.groups
    if not groups or groups[0][0] != "@":
        raise TypedStreamError("NSAttributedString without its string field")
    convert = _Converter()
    text = convert.string(groups[0][1][0])

    dicts: list[Mapping[str, AttributeValue]] = []
    runs: list[AttributeRun] = []
    offset = 0
    i = 1
    while i < len(groups):
        field_type, field_values = groups[i]
        i += 1
        if field_type != "iI":
            raise TypedStreamError(f"expected an 'iI' run group, got {field_type!r}")
        index, length = field_values
        assert isinstance(index, int) and isinstance(length, int)
        if index == len(dicts) + 1:
            if i >= len(groups) or groups[i][0] != "@":
                raise TypedStreamError(f"run with new dictionary index {index} but no dictionary")
            dicts.append(convert.dictionary(groups[i][1][0]))
            i += 1
        elif not 1 <= index <= len(dicts):
            raise TypedStreamError(f"dictionary index {index} with {len(dicts)} dictionaries seen")
        runs.append(AttributeRun(offset, offset + length, dicts[index - 1]))
        offset += length
    units = _utf16_length(text)
    if offset != units:
        raise TypedStreamError(f"run lengths sum to {offset}, text is {units} UTF-16 units")
    return AttributedBody(text=text, runs=tuple(runs), root_class=root.chain[0])


def try_parse_attributed_body(data: bytes | None) -> AttributedBody | None:
    """:func:`parse_attributed_body`, or ``None`` for ``None``/empty input and any malformed blob.

    Never raises.  ``RecursionError`` is caught as a backstop: the reader
    bounds every depth by counting rather than by recursing, so it should
    never occur, but ``Message.body`` and ``tail --parts`` promise not to
    raise and a crafted blob must not be able to break that promise.
    """
    if not data:
        return None
    try:
        return parse_attributed_body(data)
    except (TypedStreamError, UnicodeDecodeError, RecursionError):
        return None


# ---------------------------------------------------------------------------
# message parts
# ---------------------------------------------------------------------------


def _json_value(value: AttributeValue) -> Any:
    """``AttributeValue`` as JSON-ready data (CLI ``--parts`` rendering)."""
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Url):
        return value.relative
    if isinstance(value, UnknownValue):
        return {"class": value.class_name}
    if isinstance(value, Mapping):
        return {k: _json_value(v) for k, v in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class Mention:
    """A mentioned handle and its code-point span in the owning ``TextPart.text``."""

    handle: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        """``{"handle", "start", "end"}``."""
        return {"handle": self.handle, "start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class TextPart:
    """A run of text: its code points, part index, mentions and link URLs."""

    text: str
    part_index: int | None
    mentions: tuple[Mention, ...] = ()
    links: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """``{"kind": "text", "part_index", "text", "mentions": [...], "links": [...]}``."""
        return {
            "kind": "text",
            "part_index": self.part_index,
            "text": self.text,
            "mentions": [m.to_dict() for m in self.mentions],
            "links": list(self.links),
        }


@dataclass(frozen=True, slots=True)
class AttachmentPart:
    """A U+FFFC placeholder carrying an attachment guid."""

    guid: str
    part_index: int | None

    def to_dict(self) -> dict[str, Any]:
        """``{"kind": "attachment", "part_index", "guid"}``."""
        return {"kind": "attachment", "part_index": self.part_index, "guid": self.guid}


@dataclass(frozen=True, slots=True)
class UnknownPart:
    """A part with no text meaning: an empty run, or a breadcrumb-style run.

    ``attributes`` are the merged attributes of its runs (later runs win).
    """

    part_index: int | None
    attributes: Mapping[str, AttributeValue]

    def to_dict(self) -> dict[str, Any]:
        """``{"kind": "unknown", "part_index", "attributes": {...}}``.

        ``bytes`` values render as ``{"base64": ...}``, :class:`Url` as its
        string, :class:`UnknownValue` as ``{"class": name}``.
        """
        return {
            "kind": "unknown",
            "part_index": self.part_index,
            "attributes": {k: _json_value(v) for k, v in self.attributes.items()},
        }


Part = TextPart | AttachmentPart | UnknownPart


def _part_index(run: AttributeRun) -> int | None:
    value = run.attributes.get(PART_KEY)
    return value if isinstance(value, int) else None


def _is_unknown(
    part_index: int | None, text: str, attributes: Mapping[str, AttributeValue]
) -> bool:
    if text == "":
        return True
    return part_index is None and bool(attributes) and set(attributes) <= _NON_TEXT_KEYS


def message_parts(body: AttributedBody) -> tuple[Part, ...]:
    """Split ``body`` into parts: consecutive runs with the same part index form one part.

    Grouping is on *consecutive equal* ``__kIMMessagePartAttributeName``
    values (``None`` when absent), so a decreasing index still starts a new
    part.  Each group becomes:

    - :class:`AttachmentPart` when it is a single one-unit U+FFFC run whose
      ``__kIMFileTransferGUIDAttributeName`` is a string;
    - :class:`UnknownPart` when its text is empty, or when it has no part
      index and its only attributes are breadcrumb markers;
    - otherwise :class:`TextPart` with the concatenated run text (a U+FFFC
      without a guid stays in the text), the mentions
      (``__kIMMentionConfirmedMention`` string values, offsets in code points
      of the part's text) and the link URLs (``__kIMLinkAttributeName``,
      first-seen order, deduplicated).
    """
    text = body.text
    parts: list[Part] = []
    # One pass converts every run's UTF-16 offsets to code points (``_run_texts``),
    # so this is linear in the text and the runs rather than runs x text.
    groups: list[list[tuple[AttributeRun, str]]] = []
    previous: object = object()
    for run, run_text in zip(body.runs, _run_texts(text, body.runs), strict=True):
        key = run.attributes.get(PART_KEY)
        if not groups or key != previous:
            groups.append([(run, run_text)])
            previous = key
        else:
            groups[-1].append((run, run_text))
    for group in groups:
        part_index = _part_index(group[0][0])
        part_text = "".join(run_text for _, run_text in group)
        if len(group) == 1 and part_text == _PLACEHOLDER:
            guid = group[0][0].attributes.get(FILE_TRANSFER_GUID_KEY)
            if isinstance(guid, str):
                parts.append(AttachmentPart(guid, part_index))
                continue
        merged: dict[str, AttributeValue] = {}
        for run, _ in group:
            merged.update(run.attributes)
        if _is_unknown(part_index, part_text, merged):
            parts.append(UnknownPart(part_index, MappingProxyType(merged)))
            continue
        mentions: list[Mention] = []
        links: list[str] = []
        position = 0
        for run, run_text in group:
            handle = run.attributes.get(MENTION_KEY)
            if isinstance(handle, str):
                mentions.append(Mention(handle, position, position + len(run_text)))
            link = run.attributes.get(LINK_KEY)
            if isinstance(link, Url):
                link = link.relative
            if isinstance(link, str) and link not in links:
                links.append(link)
            position += len(run_text)
        parts.append(TextPart(part_text, part_index, tuple(mentions), tuple(links)))
    return tuple(parts)
