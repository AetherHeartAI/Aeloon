"""Shared command metadata for CLI and slash entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aeloon.plugins._sdk.types import CommandHandler


@dataclass(frozen=True)
class CommandSpec:
    """Declarative metadata for one user-facing command."""

    name: str
    help: str
    cli_path: tuple[str, ...] | None = None
    slash_path: tuple[str, ...] | None = None
    slash_paths: tuple[tuple[str, ...], ...] = ()
    cli_aliases: tuple[tuple[str, ...], ...] = ()
    slash_aliases: tuple[tuple[str, ...], ...] = ()
    handler: CommandHandler | None = None

    def iter_slash_paths(self) -> tuple[tuple[str, ...], ...]:
        """Return all slash-visible paths for this command."""
        paths: list[tuple[str, ...]] = []
        if self.slash_path is not None:
            paths.append(self.slash_path)
        paths.extend(self.slash_paths)
        paths.extend(self.slash_aliases)
        return tuple(paths)


@dataclass(frozen=True)
class SlashSegment:
    """One immediate slash-navigation candidate."""

    segment: str
    description: str
    path: tuple[str, ...]
    has_children: bool = False

    @property
    def label(self) -> str:
        """Return the full slash label for this candidate."""
        return "/" + " ".join(self.path)


@dataclass
class CommandCatalog:
    """Minimal registry for declared command metadata."""

    _specs: dict[str, CommandSpec] = field(default_factory=dict)
    _handlers: dict[str, "CommandHandler"] = field(default_factory=dict)

    @dataclass
    class _SlashNode:
        """One node in the derived slash-command tree."""

        segment: str
        description: str = ""
        children: dict[str, "CommandCatalog._SlashNode"] = field(default_factory=dict)

    def register(self, spec: CommandSpec) -> None:
        """Add or replace one command spec."""
        self._specs[spec.name] = spec

    def register_handler(self, label: str, handler: "CommandHandler") -> None:
        """Add or replace one slash handler by exact label."""
        self._handlers[label.lower()] = handler

    def find_handler(self, label: str) -> "CommandHandler | None":
        """Return the handler matching one exact slash label."""
        return self._handlers.get(label.strip().lower())

    def extend(self, specs: list[CommandSpec] | tuple[CommandSpec, ...]) -> None:
        """Register a batch of specs in order."""
        for spec in specs:
            self.register(spec)

    def all(self) -> list[CommandSpec]:
        """Return all registered command specs."""
        return list(self._specs.values())

    def all_handlers(self) -> dict[str, "CommandHandler"]:
        """Return all registered slash handlers keyed by label."""
        return dict(self._handlers)

    def slash_commands(self) -> list[tuple[str, str]]:
        """Return slash command labels and descriptions in registration order."""
        commands: list[tuple[str, str]] = []
        seen: set[str] = set()
        for spec in self._specs.values():
            for path in spec.iter_slash_paths():
                if not path:
                    continue
                label = "/" + " ".join(path)
                if label in seen:
                    continue
                seen.add(label)
                commands.append((label, spec.help))
        return commands

    def slash_labels(self) -> list[str]:
        """Return slash command labels only."""
        return [label for label, _desc in self.slash_commands()]

    def find_slash_command(self, label: str) -> CommandSpec | None:
        """Return the spec matching one exact slash label."""
        normalized = label.strip().lower()
        if not normalized.startswith("/"):
            return None

        for spec in self._specs.values():
            for path in spec.iter_slash_paths():
                if "/" + " ".join(path).lower() == normalized:
                    return spec
        return None

    def render_help_lines(self) -> list[str]:
        """Render slash commands as a nested markdown list."""
        root = self._build_slash_tree()
        lines: list[str] = []

        def _walk(
            node: CommandCatalog._SlashNode,
            prefix: tuple[str, ...],
            depth: int,
            inherited_description: str,
        ) -> None:
            path = (*prefix, node.segment)
            label = "/" + " ".join(path) if depth == 0 else node.segment
            line = f"{'  ' * depth}- `{label}`"
            if node.description and node.description != inherited_description:
                line += f" — {node.description}"
            lines.append(line)
            next_inherited = node.description or inherited_description
            for child in node.children.values():
                _walk(child, path, depth + 1, next_inherited)

        for child in root.children.values():
            _walk(child, (), 0, "")
        return lines

    def slash_candidates(self, query: str) -> list[tuple[str, str]]:
        """Return hierarchical candidates for the current slash query."""
        return [
            (candidate.label, candidate.description) for candidate in self.slash_segments(query)
        ]

    def slash_segments(self, query: str) -> list[SlashSegment]:
        """Return immediate child segments for the current slash query."""
        context, prefix = self._parse_slash_query(query)
        tree = self._build_slash_tree()
        node = self._resolve_context_node(tree, context)
        if node is None:
            return []

        return self._match_child_segments(node, context, prefix)

    def slash_can_descend(self, query: str) -> bool:
        """Return True when the query exactly matches a node with children."""
        resolved = self._resolve_exact_slash_node(query)
        return bool(resolved and resolved.children)

    def _build_slash_tree(self) -> _SlashNode:
        """Build an ordered tree from all registered slash command paths."""
        root = self._SlashNode(segment="")
        for label, desc in self.slash_commands():
            parts = tuple(part for part in label.lstrip("/").split() if part)
            if not parts:
                continue
            node = root
            for part in parts:
                node = node.children.setdefault(part, self._SlashNode(segment=part))
            if desc and not node.description:
                node.description = desc
        return root

    def _parse_slash_query(self, query: str) -> tuple[tuple[str, ...], str]:
        """Split a partial slash command into exact context and active prefix."""
        raw = query.strip() if not query.endswith(" ") else query
        raw = raw.lstrip("/")
        if not raw.strip():
            return (), ""

        if raw.endswith(" "):
            return tuple(raw.strip().split()), ""

        parts = raw.split()
        if len(parts) == 1:
            return (), parts[0].lower()
        return tuple(part.lower() for part in parts[:-1]), parts[-1].lower()

    def _resolve_context_node(
        self,
        root: _SlashNode,
        context: tuple[str, ...],
    ) -> _SlashNode | None:
        """Resolve a lower-cased context path against the slash tree."""
        node = root
        for segment in context:
            match = next(
                (child for key, child in node.children.items() if key.lower() == segment),
                None,
            )
            if match is None:
                return None
            node = match
        return node

    def _resolve_exact_slash_node(self, query: str) -> _SlashNode | None:
        """Resolve an exact slash query to one tree node."""
        raw = query.strip().lstrip("/")
        if not raw:
            return None

        root = self._build_slash_tree()
        node = root
        for segment in raw.split():
            match = next(
                (child for key, child in node.children.items() if key.lower() == segment.lower()),
                None,
            )
            if match is None:
                return None
            node = match
        return node

    def _match_child_segments(
        self,
        node: _SlashNode,
        context: tuple[str, ...],
        prefix: str,
    ) -> list[SlashSegment]:
        """Return ordered child candidates for one tree level."""
        if not node.children:
            return []

        children = list(node.children.items())
        if not prefix:
            return [
                SlashSegment(
                    segment=child.segment,
                    description=child.description,
                    path=(*context, child.segment),
                    has_children=bool(child.children),
                )
                for _key, child in children
            ]

        descriptions = {key.lower(): child.description for key, child in children}
        original_keys = {key.lower(): key for key, _child in children}
        child_lookup = {key.lower(): child for key, child in children}

        matched = [key.lower() for key, _child in children if key.lower().startswith(prefix)]
        if matched:
            return [
                SlashSegment(
                    segment=original_keys[key],
                    description=descriptions[key],
                    path=(*context, original_keys[key]),
                    has_children=bool(child_lookup[key].children),
                )
                for key in matched
            ]
        return []
