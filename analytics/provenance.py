"""
Data Provenance Envelopes.
Provides structured origin, timestamp, bar count, and field availability tracking
for market data snapshots, ensuring missing or estimated data is never presented as measured fact.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Tuple, Optional


@dataclass(frozen=True)
class Provenance:
    """
    Immutable envelope carrying data source, timestamp, and field completeness status.
    """
    source: str                         # "robinhood" | "yahoo" | "synthetic" | "default"
    as_of: datetime
    bar_count: int
    adjusted: bool = False
    fields_estimated: Tuple[str, ...] = ()
    fields_unavailable: Tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        source: str,
        bar_count: int,
        adjusted: bool = False,
        fields_estimated: Tuple[str, ...] = (),
        fields_unavailable: Tuple[str, ...] = (),
        as_of: Optional[datetime] = None
    ) -> "Provenance":
        return cls(
            source=source,
            as_of=as_of or datetime.now(timezone.utc),
            bar_count=bar_count,
            adjusted=adjusted,
            fields_estimated=tuple(fields_estimated),
            fields_unavailable=tuple(fields_unavailable),
        )
