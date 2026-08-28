"""Backup execution pipelines."""

import collections
import dataclasses
import inspect
import logging
import typing

import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupError, BackupOperation, DriverSource
from yaesm.driver.driverbase import DriverBase
from yaesm.errors import YaesmError
from yaesm.representation import DataProperty, Representation

logger = logging.getLogger(__name__)


class PipelineError(BackupError):
    """Raised when a backup pipeline cannot be built or executed."""


@dataclasses.dataclass(frozen=True)
class PipelineStep:
    """One driver capability invocation in a pipeline."""

    driver: DriverBase
    capability: str


_Route: ty.TypeAlias = tuple[
    type[Representation],
    frozenset[DataProperty],
    tuple[PipelineStep, ...],
]


@dataclasses.dataclass(frozen=True)
class IncrementalBase:
    """Matching source and destination states used for incremental transfers."""

    source: Representation | None
    destination: Representation | None
    created_at: ty.datetime


@dataclasses.dataclass(frozen=True, init=False)
class Pipeline:
    """An ordered sequence of driver capability invocations."""

    source: DriverSource | BackupArtifact
    destination: DriverBase
    steps: tuple[PipelineStep, ...]
    requirements: frozenset[DataProperty]

    def __init__(
        self,
        source: DriverSource | BackupArtifact,
        destination: DriverBase,
        drivers: ty.Sequence[DriverBase] = (),
        requirements: ty.Iterable[DataProperty] = (),
    ) -> None:
        required = frozenset(requirements)
        steps = _resolve(source, destination, drivers, required)
        for step in steps:
            if (
                step.driver.capability_metadata(step.capability).temporary
                and "cleanup" not in step.driver.capabilities()
            ):
                raise PipelineError(
                    f"{step.driver.name()}.{step.capability} produces a temporary "
                    "representation but the driver provides no cleanup capability"
                )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "requirements", required)

    def execute(
        self,
        operation: BackupOperation,
        base: IncrementalBase | None = None,
    ) -> BackupArtifact:
        """Execute the resolved capabilities for one backup operation."""
        value: object | None = (
            None if isinstance(self.source, DriverSource) else self.source.representation
        )
        artifact: BackupArtifact | None = None
        temporaries: list[tuple[PipelineStep, Representation]] = []
        try:
            for step in self.steps:
                method = step.driver.capability_method(step.capability)
                metadata = step.driver.capability_metadata(step.capability)
                logger.info(
                    "backup %r: %s.%s",
                    operation.backup_name,
                    step.driver.name(),
                    step.capability,
                )
                step_base = (
                    None if base is None or metadata.base is None else getattr(base, metadata.base)
                )

                try:
                    if step.capability == "source":
                        value = method()
                    elif step.capability in {"store", "import"}:
                        value = method(value, operation, step_base)
                    elif step.capability == "export":
                        value = method(value, step_base)
                    else:
                        value = method(value)
                except YaesmError as error:
                    raise PipelineError(
                        f"backup {operation.backup_name!r} failed in "
                        f"{step.driver.name()}.{step.capability}"
                    ) from error

                if metadata.temporary and isinstance(value, Representation):
                    temporaries.append((step, value))

            if not isinstance(value, BackupArtifact):
                final_step = self.steps[-1]
                raise PipelineError(
                    f"backup {operation.backup_name!r}: "
                    f"{final_step.driver.name()}.{final_step.capability} "
                    "did not produce a backup artifact"
                )
            artifact = value
            return artifact
        finally:
            final_representation = None if artifact is None else artifact.representation
            for step, representation in reversed(temporaries):
                if representation is final_representation:
                    continue
                try:
                    step.driver.cap_cleanup(representation)
                except YaesmError as error:
                    raise PipelineError(
                        f"backup {operation.backup_name!r} failed while cleaning up "
                        f"{step.driver.name()}.{step.capability}"
                    ) from error


def _resolve(
    source: DriverSource | BackupArtifact,
    destination: DriverBase,
    drivers: ty.Sequence[DriverBase],
    requirements: frozenset[DataProperty],
) -> tuple[PipelineStep, ...]:
    storage_steps = _storage_steps(destination)
    if not storage_steps:
        raise PipelineError(
            "cannot build backup pipeline:\n"
            f"  destination driver {destination.name()} provides no storage capability"
        )

    if isinstance(source, DriverSource):
        driver = source.driver
        if "source" not in driver.pipeline_capabilities():
            raise PipelineError(f"{driver.name()} driver cannot provide a backup source")
        first = PipelineStep(driver, "source")
        source_type = _output_type(first)
        if not issubclass(source_type, Representation):
            raise PipelineError("source capability does not produce a representation")
        properties = driver.capability_metadata("source").adds
        steps = (first,)
        used = frozenset({(id(driver), "source")})
        available = (driver, *drivers, destination)
    else:
        source_type = type(source.representation)
        properties = frozenset()
        steps = ()
        used = frozenset()
        available = (*drivers, destination)

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
                steps,
                used,
            )
        ]
    )
    furthest = (source_type, properties, steps)
    missing_route: tuple[frozenset[DataProperty], tuple[PipelineStep, ...]] | None = None
    required_route: _Route | None = None

    while queue:
        current_type, properties, steps, used = queue.popleft()
        advanced = False
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
                    if missing_route is None or len(missing) < len(missing_route[0]):
                        missing_route = (missing, next_steps)
                    continue
                if not metadata.requires <= properties:
                    continue
                if issubclass(output_type, Representation):
                    queue.append((output_type, next_properties, next_steps, used | {step_id}))
                    advanced = True
                    if len(next_steps) > len(furthest[2]):
                        furthest = (output_type, next_properties, next_steps)
        if (
            requirements
            and requirements <= properties
            and not advanced
            and not any(issubclass(current_type, _input_type(step)) for step in storage_steps)
            and required_route is None
        ):
            required_route = (current_type, properties, steps)

    if required_route is not None:
        raise _incompatible_pipeline_error(
            required_route,
            storage_steps,
            requirements_met=True,
        )

    if missing_route is not None:
        missing, steps = missing_route
        raise PipelineError(
            "cannot build backup pipeline:\n"
            f"  compatible route: {_format_steps(steps)}\n"
            f"  missing required properties: {_format_properties(missing)}"
        )

    raise _incompatible_pipeline_error(furthest, storage_steps)


def _incompatible_pipeline_error(
    route: _Route,
    storage_steps: ty.Sequence[PipelineStep],
    *,
    requirements_met: bool = False,
) -> PipelineError:
    representation, properties, steps = route
    route_label = "route satisfying requirements" if requirements_met else "last usable route"
    accepted = ", ".join(
        f"{_input_type(step).__name__} via {step.driver.name()}.{step.capability}"
        for step in storage_steps
    )
    return PipelineError(
        "cannot build backup pipeline:\n"
        f"  {route_label}: {_format_steps(steps) or 'existing artifact'}\n"
        f"  produced: {representation.__name__}\n"
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
