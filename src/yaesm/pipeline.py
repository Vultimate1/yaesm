"""Backup execution pipelines."""

import collections
import dataclasses
import inspect
import typing

import yaesm.ty as ty
from yaesm.backup import BackupArtifact
from yaesm.driver.driverbase import DriverBase
from yaesm.representation import DataProperty, Representation


class PipelineError(Exception):
    """Raised when a valid backup pipeline cannot be constructed."""


@dataclasses.dataclass(frozen=True)
class PipelineStep:
    """One driver capability invocation in a pipeline."""

    driver: DriverBase
    capability: str


@dataclasses.dataclass(frozen=True)
class IncrementalBase:
    """Matching source and destination states used for incremental transfers."""

    source: Representation
    destination: Representation
    created_at: ty.datetime


@dataclasses.dataclass(frozen=True, init=False)
class Pipeline:
    """An ordered sequence of driver capability invocations."""

    steps: tuple[PipelineStep, ...]
    requirements: frozenset[DataProperty]

    def __init__(
        self,
        source: DriverBase,
        destination: DriverBase,
        drivers: ty.Sequence[DriverBase] = (),
        requirements: ty.Iterable[DataProperty] = (),
    ) -> None:
        required = frozenset(requirements)
        object.__setattr__(self, "steps", _resolve(source, destination, drivers, required))
        object.__setattr__(self, "requirements", required)


def _resolve(
    source: DriverBase,
    destination: DriverBase,
    drivers: ty.Sequence[DriverBase],
    requirements: frozenset[DataProperty],
) -> tuple[PipelineStep, ...]:
    if "source" not in source.pipeline_capabilities():
        raise PipelineError(f"{source.name()} driver cannot provide a backup source")

    storage_steps = _storage_steps(destination)
    if not storage_steps:
        raise PipelineError(
            "cannot build backup pipeline:\n"
            f"  destination driver {destination.name()} provides no storage capability"
        )

    available = (source, *drivers, destination)
    first = PipelineStep(source, "source")
    source_type = _output_type(first)
    if not issubclass(source_type, Representation):
        raise PipelineError("source capability does not produce a representation")
    properties = source.capability_metadata("source").adds
    queue: collections.deque[
        tuple[
            type[Representation],
            frozenset[DataProperty],
            tuple[PipelineStep, ...],
            frozenset[tuple[int, str]],
        ]
    ] = collections.deque(
        [
            (
                source_type,
                properties,
                (first,),
                frozenset({(id(first.driver), first.capability)}),
            )
        ]
    )
    furthest = (source_type, properties, (first,))
    missing_route: tuple[frozenset[DataProperty], tuple[PipelineStep, ...]] | None = None

    while queue:
        current_type, properties, steps, used = queue.popleft()
        for driver in available:
            for capability in sorted(driver.pipeline_capabilities() - {"source"}):
                step = PipelineStep(driver, capability)
                step_id = (id(driver), capability)
                metadata = driver.capability_metadata(capability)
                if step_id in used or not issubclass(current_type, _input_type(step)):
                    continue

                output_type = _output_type(step)
                next_properties = properties | metadata.adds
                next_steps = (*steps, step)
                if driver is destination and issubclass(output_type, BackupArtifact):
                    missing = (metadata.requires - properties) | (requirements - next_properties)
                    if not missing:
                        return next_steps
                    if missing_route is None:
                        missing_route = (missing, next_steps)
                    continue
                if not metadata.requires <= properties:
                    continue
                if issubclass(output_type, Representation):
                    queue.append((output_type, next_properties, next_steps, used | {step_id}))
                    if len(next_steps) > len(furthest[2]):
                        furthest = (output_type, next_properties, next_steps)

    if missing_route is not None:
        missing, steps = missing_route
        raise PipelineError(
            "cannot build backup pipeline:\n"
            f"  compatible route: {_format_steps(steps)}\n"
            f"  missing required properties: {_format_properties(missing)}"
        )

    current_type, properties, steps = furthest
    accepted = ", ".join(sorted({_input_type(step).__name__ for step in storage_steps}))
    raise PipelineError(
        "cannot build backup pipeline:\n"
        f"  last usable route: {_format_steps(steps)}\n"
        f"  produced: {current_type.__name__}\n"
        f"  available properties: {_format_properties(properties)}\n"
        f"  destination accepts: {accepted}"
    )


def _storage_steps(driver: DriverBase) -> tuple[PipelineStep, ...]:
    steps = []
    for capability in sorted(driver.pipeline_capabilities() - {"source"}):
        step = PipelineStep(driver, capability)
        if issubclass(_output_type(step), BackupArtifact):
            steps.append(step)
    return tuple(steps)


def _format_steps(steps: ty.Sequence[PipelineStep]) -> str:
    return " -> ".join(f"{step.driver.name()}.{step.capability}" for step in steps)


def _format_properties(properties: ty.Iterable[DataProperty]) -> str:
    return ", ".join(sorted(prop.value for prop in properties)) or "none"


def _input_type(step: PipelineStep) -> type[Representation]:
    method = step.driver.capability_method(step.capability)
    parameters = tuple(inspect.signature(method).parameters.values())
    if not parameters:
        raise PipelineError(f"{step.capability} capability does not accept a representation")
    input_type = typing.get_type_hints(method)[parameters[0].name]
    if not isinstance(input_type, type) or not issubclass(input_type, Representation):
        raise PipelineError(f"{step.capability} capability has an invalid input type")
    return input_type


def _output_type(step: PipelineStep) -> type[Representation] | type[BackupArtifact]:
    method = step.driver.capability_method(step.capability)
    annotation = typing.get_type_hints(method)["return"]
    output_type = typing.get_origin(annotation) or annotation
    if not isinstance(output_type, type) or not issubclass(
        output_type, Representation | BackupArtifact
    ):
        raise PipelineError(f"{step.capability} capability has an invalid output type")
    return output_type
