"""The restricted YAML loader (SPEC §6.2): safe constructors only, and additionally no duplicate keys,
no aliases or anchors, no explicit tags and no merge keys."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from yaml.composer import Composer
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent, MappingStartEvent, ScalarEvent, SequenceStartEvent

from inspection_reconcile.errors import RunError

_MERGE_TAG = "tag:yaml.org,2002:merge"


def _reject(message: str, mark: Any) -> ConstructorError:
    return ConstructorError(None, None, message, mark)


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects the YAML features a closed configuration never needs."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise _reject("aliases are not allowed", self.peek_event().start_mark)
        event = self.peek_event()
        if isinstance(event, ScalarEvent | MappingStartEvent | SequenceStartEvent):
            if event.anchor is not None:
                raise _reject("anchors are not allowed", event.start_mark)
            if event.tag is not None:
                raise _reject("explicit tags are not allowed", event.start_mark)
        return Composer.compose_node(self, parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        if not isinstance(node, yaml.MappingNode):
            raise _reject("expected a mapping", node.start_mark)
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            if key_node.tag == _MERGE_TAG:
                raise _reject("merge keys are not allowed", key_node.start_mark)
            key = self.construct_object(key_node, deep=True)
            try:
                hash(key)
            except TypeError as exc:
                raise _reject("unhashable key", key_node.start_mark) from exc
            if key in seen:
                raise _reject(f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


# YAML 1.1 also reads 0100 as octal 64, 1:30 as sexagesimal 90, 0x64 as hexadecimal and 1_000 with separators,
# all silently. A configuration integer is plain decimal: every other form stays a string, which the closed-world
# validators then reject where an integer is expected ("fid: 010" must not quietly become field 8).
_INT_TAG = "tag:yaml.org,2002:int"
StrictLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != _INT_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictLoader.add_implicit_resolver(_INT_TAG, re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"), list("-+0123456789"))


def loads_yaml(text: str, source: str) -> Any:
    try:
        return yaml.load(text, Loader=StrictLoader)  # noqa: S506 - StrictLoader is a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise RunError("CONFIG_INVALID", f"{source}: {exc}") from exc


def load_yaml(path: Path) -> Any:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RunError("CONFIG_UNREADABLE", f"{path.as_posix()}: {exc.strerror or exc}") from exc
    try:
        text = data.decode("utf-8-sig")  # strips a leading byte-order mark
    except UnicodeDecodeError as exc:
        raise RunError("CONFIG_INVALID", f"{path.as_posix()}: not valid UTF-8") from exc
    return loads_yaml(text, path.as_posix())
