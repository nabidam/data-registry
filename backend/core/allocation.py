"""Sample allocation: the single lifecycle flag every sample carries.

Replaces the old ``status`` (active|ignored) column. Allocation is decided at
ingestion time — before a sample is ever visible to the dataset builder — so
evaluation data can never leak into a training snapshot.

Invariants:
* ``TRAINABLE``           may appear in snapshots.
* ``RESERVED_EVALUATION`` never appears in any training snapshot, forever.
* ``IGNORED``             participates in neither training nor evaluation.
* ``QUARANTINED``         is held out from training after contamination checks
  but is not an evaluation example.

Reserved and quarantined samples never become trainable again; their timestamps
record the protection permanently, so un-ignoring restores the protected
allocation rather than ``TRAINABLE``.
"""

from enum import StrEnum


class Allocation(StrEnum):
    TRAINABLE = "TRAINABLE"
    RESERVED_EVALUATION = "RESERVED_EVALUATION"
    QUARANTINED = "QUARANTINED"
    IGNORED = "IGNORED"
