from fastapi import APIRouter

from api.routes import (
    annotations,
    batches,
    datasets,
    evaluation_sets,
    exports,
    imports,
    samples,
    search,
    simple,
    snapshots,
    stats,
)

api_router = APIRouter(prefix="/api")

for router in (
    simple.sources_router,
    batches.router,
    samples.router,
    annotations.router,
    datasets.router,
    simple.splits_router,
    snapshots.router,
    evaluation_sets.router,
    simple.experiments_router,
    simple.models_router,
    imports.router,
    exports.router,
    stats.router,
    search.router,
):
    api_router.include_router(router)

__all__ = ["api_router"]
