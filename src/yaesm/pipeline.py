"""Backup execution pipelines."""

import collections
import dataclasses
import inspect
import logging
import typing

import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupError, BackupOperation
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
    """A resolved sequence using every configured driver in order."""

    source_driver: DriverBase
    destination: DriverBase
    source_artifact: BackupArtifact | None
    steps: tuple[PipelineStep, ...]

    def __init__(
        self,
        source_driver: DriverBase,
        destination: DriverBase,
        drivers: ty.Sequence[DriverBase] = (),
        *,
        source_artifact: BackupArtifact | None = None,
    ) -> None:
        steps = _resolve(source_driver, destination, drivers, source_artifact)
        for step in steps:
            if (
                step.driver.capability_metadata(step.capability).temporary
                and "cleanup" not in step.driver.capabilities()
            ):
                raise PipelineError(
                    f"{step.driver.name()}.{step.capability} produces a temporary "
                    "representation but the driver provides no cleanup capability"
                )
        object.__setattr__(self, "source_driver", source_driver)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "source_artifact", source_artifact)
        object.__setattr__(self, "steps", steps)

    def execute(
        self,
        operation: BackupOperation,
        base: IncrementalBase | None = None,
    ) -> BackupArtifact:
        """Execute one backup, omitting a rejected incremental base from every step."""
        value: object | None = (
            None if self.source_artifact is None else self.source_artifact.representation
        )
        artifact: BackupArtifact | None = None
        temporaries: list[tuple[PipelineStep, Representation]] = []
        approved_base: IncrementalBase | None = None
        base_checked = base is None
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
                try:
                    if not base_checked and metadata.base is not None:
                        assert base is not None
                        if step.driver.validate_base(
                            step.capability,
                            ty.cast(Representation, value),
                            base.source,
                            base.destination,
                        ):
                            approved_base = base
                        base_checked = True
                    step_base = (
                        None
                        if approved_base is None or metadata.base is None
                        else getattr(approved_base, metadata.base)
                    )
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
    source_driver: DriverBase,
    destination: DriverBase,
    drivers: ty.Sequence[DriverBase],
    source_artifact: BackupArtifact | None,
) -> tuple[PipelineStep, ...]:
    storage_steps = _storage_steps(destination)
    if not storage_steps:
        raise PipelineError(
            "cannot build backup pipeline:\n"
            f"  destination driver {destination.name()} provides no storage capability"
        )

    required_properties: frozenset[DataProperty] = frozenset()
    if source_artifact is None:
        if "source" not in source_driver.pipeline_capabilities():
            raise PipelineError(f"{source_driver.name()} driver cannot provide a backup source")
        first = PipelineStep(source_driver, "source")
        source_type = _output_type(first)
        if not issubclass(source_type, Representation):
            raise PipelineError("source capability does not produce a representation")
        properties = source_driver.capability_metadata("source").adds
        if "snapshot" in source_driver.pipeline_capabilities():
            required_properties = frozenset({DataProperty.SNAPSHOT})
        steps = (first,)
        used = frozenset({(id(source_driver), "source")})
    else:
        source_type = type(source_artifact.representation)
        properties = frozenset()
        steps = ()
        used = frozenset()

    available = (source_driver, *drivers, destination)
    configured_indexes = {id(driver): index for index, driver in enumerate(drivers)}

    queue: collections.deque[
        tuple[
            type[Representation],
            frozenset[DataProperty],
            tuple[PipelineStep, ...],
            frozenset[tuple[int, str]],
            int,
        ]
    ] = collections.deque(
        [
            (
                source_type,
                properties,
                steps,
                used,
                0,
            )
        ]
    )
    furthest = (source_type, properties, steps)
    complete_route: _Route | None = None
    rejected_route: (
        tuple[
            int,
            frozenset[DataProperty],
            tuple[PipelineStep, ...],
        ]
        | None
    ) = None

    while queue:
        current_type, properties, steps, used, driver_index = queue.popleft()
        for driver in available:
            for capability in sorted(driver.pipeline_capabilities() - {"source"}):
                step = PipelineStep(driver, capability)
                step_id = (id(driver), capability)
                metadata = driver.capability_metadata(capability)
                if step_id in used or not issubclass(current_type, _input_type(step)):
                    continue

                configured_index = configured_indexes.get(id(driver))
                if configured_index not in (None, driver_index - 1, driver_index):
                    continue
                next_driver_index = (
                    driver_index + 1 if configured_index == driver_index else driver_index
                )

                output_type = _output_type(step)
                next_properties = properties | metadata.adds
                next_steps = (*steps, step)
                if driver is destination and issubclass(output_type, BackupArtifact):
                    missing = (metadata.requires - properties) | (
                        required_properties - next_properties
                    )
                    if not missing and next_driver_index == len(drivers):
                        return next_steps
                    rejected = (next_driver_index, missing, next_steps)
                    if rejected_route is None or len(drivers) - next_driver_index + len(
                        missing
                    ) < len(drivers) - rejected_route[0] + len(rejected_route[1]):
                        rejected_route = rejected
                    continue
                if not metadata.requires <= properties:
                    continue
                if issubclass(output_type, Representation):
                    queue.append(
                        (
                            output_type,
                            next_properties,
                            next_steps,
                            used | {step_id},
                            next_driver_index,
                        )
                    )
                    if next_driver_index == len(drivers) and complete_route is None:
                        complete_route = (output_type, next_properties, next_steps)
                    if len(next_steps) > len(furthest[2]):
                        furthest = (output_type, next_properties, next_steps)

    if rejected_route is not None and rejected_route[0] == len(drivers):
        _, missing, steps = rejected_route
        raise PipelineError(
            "cannot build backup pipeline:\n"
            f"  compatible route: {_format_steps(steps)}\n"
            f"  missing required properties: {_format_properties(missing)}"
        )
    if complete_route is not None:
        raise _incompatible_pipeline_error(complete_route, storage_steps)
    if rejected_route is not None:
        next_driver_index, _, steps = rejected_route
        raise PipelineError(
            "cannot build backup pipeline:\n"
            f"  compatible route: {_format_steps(steps)}\n"
            f"  next configured driver cannot be used: "
            f"{drivers[next_driver_index].name()}"
        )

    raise _incompatible_pipeline_error(furthest, storage_steps)


def _incompatible_pipeline_error(
    route: _Route,
    storage_steps: ty.Sequence[PipelineStep],
) -> PipelineError:
    representation, properties, steps = route
    accepted = ", ".join(
        f"{_input_type(step).__name__} via {step.driver.name()}.{step.capability}"
        for step in storage_steps
    )
    return PipelineError(
        "cannot build backup pipeline:\n"
        f"  last usable route: {_format_steps(steps) or 'existing artifact'}\n"
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
