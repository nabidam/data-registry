"""Sample allocation: the single lifecycle flag every sample carries.

Replaces the old ``status`` (active|ignored) column. Allocation is decided at
ingestion time — before a sample is ever visible to the dataset builder — so
evaluation data can never leak into a training snapshot.

Invariants:
* ``TRAINABLE``           may appear in snapshots.
* ``RESERVED_EVALUATION`` never appears in any training snapshot, forever.
* ``IGNORED``             participates in neither training nor evaluation.

A reserved sample never becomes trainable again; ``Sample.reserved_at`` records
the reservation permanently, so even un-ignoring a sample restores it to
``RESERVED_EVALUATION`` rather than ``TRAINABLE``.
"""

from enum import StrEnum


class Allocation(StrEnum):
    TRAINABLE = "TRAINABLE"
    RESERVED_EVALUATION = "RESERVED_EVALUATION"
    IGNORED = "IGNORED"
