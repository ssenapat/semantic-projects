"""Eligibility-aware view over the SP-1 catalog (REQ-046). Read-only."""
from __future__ import annotations

from ..services.internal_catalog_loader import InternalCatalog


class InternalSemanticRegistry:
    def __init__(self, internal: InternalCatalog, audience: str):
        self.internal = internal
        self.catalog = internal.catalog
        self.audience = audience

    @property
    def query_policy(self):
        return self.catalog.query_policy

    def metric(self, name: str):
        """SP-1 Metric, or None when unknown or not exposable for the audience."""
        m = self.catalog.metrics.get(name)
        if m is None or not m.status or not self.catalog.is_exposable(m.status, self.audience):
            return None
        return m

    def model_for(self, metric):
        return self.catalog.model_for(metric)

    def dimension(self, name: str):
        """SP-1 Dimension (eligible only), or None."""
        for model in self.catalog.models.values():
            d = model.dimension(name)
            if d is not None:
                status = d.status or model.status
                return d if self.catalog.is_exposable(status, self.audience) else None
        return None

    def dimension_for_metric(self, metric, name: str):
        model = self.catalog.model_for(metric)
        d = model.dimension(name)
        if d is None:
            return None
        status = d.status or model.status
        return d if self.catalog.is_exposable(status, self.audience) else None
