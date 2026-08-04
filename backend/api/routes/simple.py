"""Entities that need nothing beyond plain CRUD."""

from api.crud import crud_router
from models import Experiment, Model, Source, SplitDefinition
from schemas import (
    ExperimentIn,
    ExperimentOut,
    ModelIn,
    ModelOut,
    SourceIn,
    SourceOut,
    SplitIn,
    SplitOut,
)

sources_router = crud_router(
    model=Source, read_schema=SourceOut, create_schema=SourceIn, prefix="/sources", tag="sources"
)

splits_router = crud_router(
    model=SplitDefinition,
    read_schema=SplitOut,
    create_schema=SplitIn,
    prefix="/splits",
    tag="splits",
)

experiments_router = crud_router(
    model=Experiment,
    read_schema=ExperimentOut,
    create_schema=ExperimentIn,
    prefix="/experiments",
    tag="experiments",
)

models_router = crud_router(
    model=Model, read_schema=ModelOut, create_schema=ModelIn, prefix="/models", tag="models"
)
