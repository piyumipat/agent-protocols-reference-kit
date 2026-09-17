"""Agora Protocol Documents and their exact-text identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ProtocolDocument:
    """A parsed Agora Protocol Document while preserving its exact source text."""

    source: str
    name: str
    description: str
    multiround: bool
    specification: str

    @classmethod
    def parse(cls, source: str) -> ProtocolDocument:
        """Parse required YAML metadata and the free-form specification."""
        lines = source.splitlines(keepends=True)
        separator = next(
            (index for index, line in enumerate(lines) if line.strip() == "---"),
            None,
        )
        if separator is None:
            raise ValueError("Protocol Document must contain a line with '---'")

        metadata_text = "".join(lines[:separator])
        specification = "".join(lines[separator + 1 :])
        try:
            metadata: Any = yaml.safe_load(metadata_text)
        except yaml.YAMLError as error:
            raise ValueError("Protocol Document metadata is invalid YAML") from error
        if not isinstance(metadata, dict):
            raise TypeError("Protocol Document metadata must be a YAML object")

        name = metadata.get("name")
        description = metadata.get("description")
        multiround = metadata.get("multiround")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Protocol Document metadata requires a non-empty name")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Protocol Document metadata requires a non-empty description")
        if not isinstance(multiround, bool):
            raise TypeError("Protocol Document metadata requires a boolean multiround")
        if not specification.strip():
            raise ValueError("Protocol Document specification must not be empty")

        return cls(
            source=source,
            name=name,
            description=description,
            multiround=multiround,
            specification=specification,
        )

    @property
    def hash(self) -> str:
        """Return the standard's hexadecimal SHA-1 identifier of the full text."""
        return sha1(self.source.encode("utf-8"), usedforsecurity=False).hexdigest()


class ProtocolRegistry:
    """In-memory collection of fully supported Protocol Documents."""

    def __init__(self, documents: tuple[ProtocolDocument, ...] = ()) -> None:
        self._documents: dict[str, ProtocolDocument] = {}
        for document in documents:
            self.register(document)

    def register(self, document: ProtocolDocument) -> str:
        self._documents[document.hash] = document
        return document.hash

    def get(self, protocol_hash: str) -> ProtocolDocument | None:
        return self._documents.get(protocol_hash)

    def supports(self, protocol_hash: str) -> bool:
        return protocol_hash in self._documents

    def wellknown(self) -> dict[str, list[str]]:
        """Return the discovery representation defined by the Working Standard."""
        return {
            protocol_hash: [document.source]
            for protocol_hash, document in self._documents.items()
        }
