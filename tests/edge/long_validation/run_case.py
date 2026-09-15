"""Orchestrate one fixed Raspberry Pi 5 long-validation case."""

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import FrameType
from typing import cast

_DIGEST_REFERENCE = re.compile(r"^[A-Za-z0-9._:/-]+@sha256:([0-9a-f]{64})$")
_LOCAL_IMAGE_ID = re.compile(r"^sha256:([0-9a-f]{64})$")
_SOURCE_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_THROTTLED = re.compile(r"^throttled=0x([0-9a-fA-F]+)$")
_EXPECTED_SENSOR_IDS = ["lidar_1", "lidar_2"]
_WARMUP_S = 300
_MEASUREMENT_S = 3_600
_MAX_SAMPLES = 40_000
_CONTAINER_UID = 10_001
_GENERATOR_WAIT_TIMEOUT_S = _WARMUP_S + _MEASUREMENT_S + 120
_PROCESSING_WAIT_TIMEOUT_S = 30
_HELPER_WAIT_TIMEOUT_S = 30
_EXPECTED_LOGICAL_CPU_COUNT = 4
_MIN_PI5_8GB_MEMORY_BYTES = 7 * 1_073_741_824
_MAX_PI5_8GB_MEMORY_BYTES = 9 * 1_073_741_824
_EXPECTED_OBSERVATION_RECORDS = 3_901
_MAX_SOURCE_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_SOURCE_ARCHIVE_FILES = 10_000
_MAX_SOURCE_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
_OBSERVATION_RESULT_FIELDS = {
    "schema_version",
    "run_id",
    "generator_source_commit",
    "environment_id",
    "input_fingerprint_sha256",
    "seed",
    "scene_fingerprint_sha256",
    "received_records",
    "first_sequence",
    "last_sequence",
    "first_elapsed_s",
    "last_elapsed_s",
    "surface_stream_sha256",
    "surface_change_count",
    "minimum_surface_volume_m3",
    "maximum_surface_volume_m3",
}


class RunnerError(RuntimeError):
    """Raised when orchestration evidence is missing or inconsistent."""


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise RunnerError(f"{path} must be an object")
    return cast(Mapping[str, object], value)


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), path.name)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RunnerError(f"cannot read JSON input {path.name}") from error


def _atomic_json(path: Path, document: Mapping[str, object], *, mode: int = 0o644) -> None:
    if path.exists() or path.is_symlink():
        raise RunnerError(f"output already exists: {path.name}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(document, allow_nan=False, indent=2, sort_keys=True) + "\n"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    except FileExistsError as error:
        raise RunnerError(f"output already exists: {path.name}") from error
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def fingerprint_files(paths: Sequence[Path]) -> str:
    """Match the helper's path-independent file fingerprint."""
    names = [path.name for path in paths]
    if len(names) != len(set(names)):
        raise RunnerError("configuration file names must be unique")
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        try:
            content = path.read_bytes()
        except OSError as error:
            raise RunnerError(f"cannot fingerprint {path.name}") from error
        name = path.name.encode()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def parse_digest_reference(value: str) -> str:
    match = _DIGEST_REFERENCE.fullmatch(value)
    if match is None:
        raise RunnerError("container images must use repository@sha256 digest references")
    return f"sha256:{match.group(1)}"


def parse_processing_image_reference(value: str) -> str:
    local_match = _LOCAL_IMAGE_ID.fullmatch(value)
    if local_match is not None:
        return f"sha256:{local_match.group(1)}"
    return parse_digest_reference(value)


def parse_throttled(value: str) -> int:
    match = _THROTTLED.fullmatch(value.strip())
    if match is None:
        raise RunnerError("vcgencmd get_throttled returned an unexpected value")
    return int(match.group(1), 16)


def parse_cgroup_path(proc_root: Path, cgroup_root: Path, host_pid: int) -> tuple[Path, Path]:
    """Return exact host and helper-visible cgroup v2 paths for one PID."""
    try:
        lines = (proc_root / str(host_pid) / "cgroup").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise RunnerError("cannot read container cgroup membership") from error
    matches = [line[3:] for line in lines if line.startswith("0::/")]
    if len(matches) != 1 or ".." in Path(matches[0]).parts:
        raise RunnerError("container does not have one valid cgroup v2 membership")
    relative = matches[0].removeprefix("/")
    host_path = cgroup_root / relative
    if not (host_path / "cpu.stat").is_file() or not (host_path / "memory.current").is_file():
        raise RunnerError("container cgroup v2 counters are unavailable")
    helper_path = Path("/host/sys/fs/cgroup") / relative
    return host_path, helper_path


def _parse_cpuset(value: str) -> set[int]:
    cpus: set[int] = set()
    for item in value.strip().split(","):
        bounds = item.split("-", maxsplit=1)
        try:
            first = int(bounds[0])
            last = int(bounds[-1])
        except ValueError as error:
            raise RunnerError("cgroup effective CPU set is invalid") from error
        if first < 0 or last < first or last > 1_023:
            raise RunnerError("cgroup effective CPU set is invalid")
        cpus.update(range(first, last + 1))
    if not cpus:
        raise RunnerError("cgroup effective CPU set is empty")
    return cpus


def _cgroup_ancestors(directory: Path, root: Path) -> list[Path]:
    resolved_directory = directory.resolve()
    resolved_root = root.resolve()
    if not resolved_directory.is_relative_to(resolved_root):
        raise RunnerError("container cgroup is outside the cgroup v2 root")
    ancestors: list[Path] = []
    current = resolved_directory
    while True:
        ancestors.append(current)
        if current == resolved_root:
            return ancestors
        current = current.parent


def _read_cgroup_limits(ancestor: Path) -> tuple[float | None, int | None]:
    try:
        cpu_max = (ancestor / "cpu.max").read_text(encoding="ascii").strip().split()
        memory_max = (ancestor / "memory.max").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise RunnerError("effective cgroup resource constraints are unavailable") from error
    if len(cpu_max) != 2:
        raise RunnerError("cgroup cpu.max is invalid")
    try:
        period = int(cpu_max[1])
        quota = None if cpu_max[0] == "max" else int(cpu_max[0])
        memory = None if memory_max == "max" else int(memory_max)
    except ValueError as error:
        raise RunnerError("cgroup resource constraint value is invalid") from error
    if period <= 0 or (quota is not None and quota <= 0) or (memory is not None and memory <= 0):
        raise RunnerError("cgroup resource constraint value is not positive")
    return (None if quota is None else quota / period), memory


def _read_effective_constraints(component: str, directory: Path, root: Path) -> dict[str, object]:
    cpu_quota_cores: float | None = None
    memory_limit_bytes: int | None = None
    for ancestor in _cgroup_ancestors(directory, root):
        quota, memory = _read_cgroup_limits(ancestor)
        if quota is not None:
            cpu_quota_cores = quota if cpu_quota_cores is None else min(cpu_quota_cores, quota)
        if memory is not None:
            memory_limit_bytes = (
                memory if memory_limit_bytes is None else min(memory_limit_bytes, memory)
            )
    try:
        effective_cpus = _parse_cpuset(
            (directory / "cpuset.cpus.effective").read_text(encoding="ascii")
        )
    except (OSError, UnicodeError) as error:
        raise RunnerError("effective cgroup CPU set is unavailable") from error
    if len(effective_cpus) != _EXPECTED_LOGICAL_CPU_COUNT:
        raise RunnerError("container cgroup must expose all four Raspberry Pi CPUs")
    if cpu_quota_cores is not None and cpu_quota_cores < _EXPECTED_LOGICAL_CPU_COUNT:
        raise RunnerError("container cgroup CPU quota is below four cores")
    if memory_limit_bytes is not None and memory_limit_bytes < _MIN_PI5_8GB_MEMORY_BYTES:
        raise RunnerError("container cgroup memory limit is below the 8 GB device range")
    return {
        "component": component,
        "effective_cpu_count": len(effective_cpus),
        "cpu_quota_cores": cpu_quota_cores,
        "memory_limit_bytes": memory_limit_bytes,
    }


def derive_lifecycle(
    *,
    run_id: str,
    initial_throttled: int,
    final_throttled: int,
    initial_states: Mapping[str, Mapping[str, object]],
    final_states: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, object], dict[str, object]]:
    """Derive strict counters and retain their local evidence separately."""
    if initial_throttled != 0:
        raise RunnerError("thermal throttle history is not clean at validation start")
    components = ("generator", "processing")
    restart_count = 0
    oom_events = 0
    unclassified = 0
    evidence_components: list[dict[str, object]] = []
    for component in components:
        initial = _mapping(initial_states.get(component), f"initial {component} state")
        final = _mapping(final_states.get(component), f"final {component} state")
        initial_restarts = initial.get("restart_count")
        final_restarts = final.get("restart_count")
        exit_code = final.get("exit_code")
        oom_killed = final.get("oom_killed")
        if (
            isinstance(initial_restarts, bool)
            or not isinstance(initial_restarts, int)
            or isinstance(final_restarts, bool)
            or not isinstance(final_restarts, int)
            or final_restarts < initial_restarts
        ):
            raise RunnerError(f"{component} restart evidence is invalid")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise RunnerError(f"{component} exit evidence is invalid")
        if not isinstance(oom_killed, bool):
            raise RunnerError(f"{component} OOM evidence is invalid")
        restart_count += final_restarts - initial_restarts
        oom_events += int(oom_killed)
        unclassified += int(exit_code != 0)
        evidence_components.append(
            {
                "component": component,
                "exit_code": exit_code,
                "oom_killed": oom_killed,
                "restart_count_after": final_restarts,
                "restart_count_before": initial_restarts,
            }
        )
    thermal_events = int(final_throttled != 0)
    lifecycle: dict[str, object] = {
        "schema_version": "long-validation-lifecycle-result.v1",
        "container_restarts": restart_count,
        "oom_events": oom_events,
        "thermal_throttling_events": thermal_events,
        "unclassified_loss_windows": unclassified,
    }
    evidence: dict[str, object] = {
        "schema_version": "long-validation-lifecycle-evidence.v1",
        "run_id": run_id,
        "containers": evidence_components,
        "thermal": {
            "get_throttled_after": f"0x{final_throttled:x}",
            "get_throttled_before": f"0x{initial_throttled:x}",
        },
    }
    return lifecycle, evidence


@dataclass(frozen=True, slots=True)
class PublicConfiguration:
    source_paths: tuple[Path, Path, Path, Path]
    seed: int
    environment_id: str
    site_id: str
    edge_id: str
    config_revision: str
    calibration_version: str
    pinned_processing_source_commit: str
    validation_processing_source_commit: str


def _load_processing_source_commits(repository: Path) -> tuple[str, str]:
    source_metadata = _load_json(repository / "edge-platform-integration" / "SOURCE.json")
    commit = source_metadata.get("commit")
    if not isinstance(commit, str) or _SOURCE_COMMIT.fullmatch(commit) is None:
        raise RunnerError("edge platform source metadata has an invalid pinned commit")
    validation_image = _mapping(
        source_metadata.get("validation_image"), "edge platform validation image"
    )
    validation_commit = validation_image.get("commit")
    if (
        not isinstance(validation_commit, str)
        or _SOURCE_COMMIT.fullmatch(validation_commit) is None
    ):
        raise RunnerError("edge platform source metadata has an invalid validation commit")
    patch_name = validation_image.get("patch_path")
    patch_sha256 = validation_image.get("patch_sha256")
    if (
        not isinstance(patch_name, str)
        or not isinstance(patch_sha256, str)
        or _SHA256.fullmatch(patch_sha256) is None
    ):
        raise RunnerError("edge platform validation patch metadata is incomplete")
    integration_root = (repository / "edge-platform-integration").resolve()
    patch_path = (integration_root / patch_name).resolve()
    if not patch_path.is_relative_to(integration_root) or not patch_path.is_file():
        raise RunnerError("edge platform validation patch path is invalid")
    if hashlib.sha256(patch_path.read_bytes()).hexdigest() != patch_sha256:
        raise RunnerError("edge platform validation patch digest does not match")
    return commit, validation_commit


def load_public_configuration(repository: Path) -> PublicConfiguration:
    generator_path = repository / "examples" / "generator.v2.json"
    processing_path = repository / "edge-platform-integration" / "v1" / "processing.synthetic.json"
    pinned_commit, validation_commit = _load_processing_source_commits(repository)
    return _load_public_configuration_files(
        generator_path,
        processing_path,
        pinned_commit,
        validation_commit,
    )


def _load_public_configuration_files(
    generator_path: Path,
    processing_path: Path,
    pinned_processing_source_commit: str,
    validation_processing_source_commit: str,
) -> PublicConfiguration:
    environment_path, quality_path, seed, environment_id = _load_generator_values(generator_path)
    site_id, edge_id, config_revision, calibration_version = _load_processing_values(
        processing_path
    )
    return PublicConfiguration(
        source_paths=(generator_path, environment_path, quality_path, processing_path),
        seed=seed,
        environment_id=environment_id,
        site_id=site_id,
        edge_id=edge_id,
        config_revision=config_revision,
        calibration_version=calibration_version,
        pinned_processing_source_commit=pinned_processing_source_commit,
        validation_processing_source_commit=validation_processing_source_commit,
    )


def _load_generator_values(generator_path: Path) -> tuple[Path, Path, int, str]:
    generator = _load_json(generator_path)
    environment_name = generator.get("environment_path")
    quality_name = generator.get("quality_profile_path")
    if not isinstance(environment_name, str) or not isinstance(quality_name, str):
        raise RunnerError("generator configuration references are invalid")
    if Path(environment_name).name != environment_name or Path(quality_name).name != quality_name:
        raise RunnerError("generator configuration references must be local file names")
    environment_path = generator_path.parent / environment_name
    quality_path = generator_path.parent / quality_name
    environment = _load_json(environment_path)
    environment_id = environment.get("environment_id")
    if not isinstance(environment_id, str) or not environment_id:
        raise RunnerError("public environment identifier is invalid")
    sensors = environment.get("sensors")
    if not isinstance(sensors, list):
        raise RunnerError("public environment sensors must be an array")
    sensor_ids = [
        _mapping(sensor, "public environment sensor").get("sensor_id") for sensor in sensors
    ]
    if sensor_ids != _EXPECTED_SENSOR_IDS:
        raise RunnerError("public environment must contain lidar_1 and lidar_2 in order")
    seed = generator.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise RunnerError("public generator seed must be a non-negative integer")
    return environment_path, quality_path, seed, environment_id


def _load_processing_values(processing_path: Path) -> tuple[str, str, str, str]:
    processing = _load_json(processing_path)
    processing_sensors = processing.get("sensors")
    if not isinstance(processing_sensors, list):
        raise RunnerError("public processing sensors must be an array")
    processing_sensor_ids = [
        _mapping(sensor, "public processing sensor").get("sensor_id")
        for sensor in processing_sensors
    ]
    endpoints = [
        _mapping(sensor, "public processing sensor").get("endpoint")
        for sensor in processing_sensors
    ]
    if processing_sensor_ids != _EXPECTED_SENSOR_IDS or endpoints != [
        "unix:/sockets/lidar_1.sock",
        "unix:/sockets/lidar_2.sock",
    ]:
        raise RunnerError("public processing fixture does not target both simulator UDS lanes")
    identities = [processing.get(name) for name in ("site_id", "edge_id", "config_revision")]
    if any(not isinstance(value, str) or not value for value in identities):
        raise RunnerError("public processing identities are invalid")
    calibration = _mapping(processing.get("calibration"), "public processing calibration")
    calibration_version = calibration.get("version")
    if not isinstance(calibration_version, str) or not calibration_version:
        raise RunnerError("public processing calibration version is invalid")
    return (
        cast(str, identities[0]),
        cast(str, identities[1]),
        cast(str, identities[2]),
        calibration_version,
    )


def _load_staged_public_configuration(
    directory: Path,
    pinned_processing_source_commit: str,
    validation_processing_source_commit: str,
) -> PublicConfiguration:
    return _load_public_configuration_files(
        directory / "generator.v2.json",
        directory / "processing.synthetic.json",
        pinned_processing_source_commit,
        validation_processing_source_commit,
    )


def _sha256_stream(source: object) -> str:
    digest = hashlib.sha256()
    read = getattr(source, "read", None)
    if not callable(read):
        raise RunnerError("source archive contains an unreadable file")
    while chunk := read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def verify_repository_checkout(repository: Path, expected_commit: str) -> None:
    """Require a development-side checkout that matches the candidate source."""
    commands = (
        (["rev-parse", "--show-toplevel"], "repository root"),
        (["rev-parse", "HEAD"], "repository HEAD"),
        (["status", "--porcelain=v1", "--untracked-files=all"], "repository status"),
    )
    outputs: list[str] = []
    for arguments, label in commands:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RunnerError(f"cannot inspect {label}") from error
        if result.returncode != 0:
            raise RunnerError(f"cannot inspect {label}")
        outputs.append(result.stdout.strip())
    root, head, status = outputs
    if Path(root).resolve() != repository.resolve():
        raise RunnerError("validation repository path is not its Git worktree root")
    if head != expected_commit:
        raise RunnerError("validation checkout HEAD differs from generator source commit")
    if status:
        raise RunnerError("validation checkout contains tracked or untracked changes")


def _archive_member_path(repository: Path, member: tarfile.TarInfo) -> tuple[Path, str]:
    raw_name = member.name.rstrip("/")
    relative = PurePosixPath(raw_name)
    if (
        not raw_name
        or relative.is_absolute()
        or relative.as_posix() != raw_name
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise RunnerError("source archive contains an unsafe path")
    return repository.joinpath(*relative.parts), relative.as_posix()


def _verify_regular_archive_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path
) -> None:
    if target.is_symlink() or not target.is_file():
        raise RunnerError("source tree file differs from its archive")
    archived = archive.extractfile(member)
    if archived is None:
        raise RunnerError("source archive contains an unreadable file")
    with archived, target.open("rb") as current:
        if _sha256_stream(archived) != _sha256_stream(current):
            raise RunnerError("source tree file content differs from its archive")
    expected_executable = bool(member.mode & 0o111)
    actual_executable = bool(target.stat().st_mode & 0o111)
    if actual_executable != expected_executable:
        raise RunnerError("source tree file mode differs from its archive")


def _verify_archive_member(
    archive: tarfile.TarFile, repository: Path, member: tarfile.TarInfo
) -> tuple[str, int | None]:
    target, relative_name = _archive_member_path(repository, member)
    if member.isdir():
        if target.is_symlink() or not target.is_dir():
            raise RunnerError("source tree directory differs from its archive")
        return relative_name, None
    if member.isfile():
        _verify_regular_archive_member(archive, member, target)
        return relative_name, member.size
    if member.issym():
        if not target.is_symlink() or os.readlink(target) != member.linkname:
            raise RunnerError("source tree symlink differs from its archive")
        return relative_name, 0
    raise RunnerError("source archive contains an unsupported entry type")


def _verify_archive_contents(
    archive: tarfile.TarFile, repository: Path, expected_commit: str
) -> set[str]:
    if archive.pax_headers.get("comment") != expected_commit:
        raise RunnerError("source archive commit differs from generator source commit")
    archived_entries: set[str] = set()
    archived_files: set[str] = set()
    expanded_bytes = 0
    for index, member in enumerate(archive):
        if index >= _MAX_SOURCE_ARCHIVE_FILES:
            raise RunnerError("source archive contains too many entries")
        if member.isfile() and expanded_bytes + member.size > _MAX_SOURCE_ARCHIVE_EXPANDED_BYTES:
            raise RunnerError("source archive exceeds the expanded size limit")
        relative_name, file_size = _verify_archive_member(archive, repository, member)
        if relative_name in archived_entries:
            raise RunnerError("source archive contains a duplicate path")
        archived_entries.add(relative_name)
        if file_size is None:
            continue
        archived_files.add(relative_name)
        expanded_bytes += file_size
    return archived_files


def verify_repository_archive(repository: Path, source_archive: Path, expected_commit: str) -> str:
    """Require an exact extraction of one commit-addressed Git archive."""
    try:
        archive_stat = source_archive.stat()
    except OSError as error:
        raise RunnerError("cannot inspect the source archive") from error
    if source_archive.is_symlink() or not source_archive.is_file():
        raise RunnerError("source archive must be a regular file")
    if archive_stat.st_size > _MAX_SOURCE_ARCHIVE_BYTES:
        raise RunnerError("source archive exceeds the compressed size limit")
    archive_sha256 = hashlib.sha256(source_archive.read_bytes()).hexdigest()
    try:
        with tarfile.open(source_archive, mode="r:*") as archive:
            archived_files = _verify_archive_contents(archive, repository, expected_commit)
    except (OSError, tarfile.TarError, UnicodeError) as error:
        raise RunnerError("cannot verify the source archive") from error

    current_files = {
        path.relative_to(repository).as_posix()
        for path in repository.rglob("*")
        if path.is_symlink() or not path.is_dir()
    }
    if current_files != archived_files:
        raise RunnerError("source tree file set differs from its archive")
    return archive_sha256


class Docker:
    """Small Docker CLI boundary that never reports command arguments on failure."""

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def _run(
        self,
        arguments: Sequence[str],
        *,
        operation: str,
        capture: bool = True,
        check: bool = True,
        timeout_s: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self.executable, *arguments],
                check=False,
                text=True,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            raise RunnerError(f"Docker {operation} timed out") from error
        if check and result.returncode != 0:
            raise RunnerError(f"Docker {operation} failed")
        return result

    def version(self) -> str:
        value = self._run(
            ["version", "--format", "{{.Server.Version}}"], operation="version query"
        ).stdout.strip()
        if not value:
            raise RunnerError("Docker server version is unavailable")
        return value

    def pull(self, image: str) -> None:
        self._run(["pull", image], operation="image pull", capture=False)

    def verify_image(
        self, image: str, expected_source_commit: str, *, allow_local_id: bool = False
    ) -> None:
        digest = (
            parse_processing_image_reference(image)
            if allow_local_id
            else parse_digest_reference(image)
        )
        platform_name = self._run(
            ["image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image],
            operation="image platform inspection",
        ).stdout.strip()
        if platform_name != "linux/arm64":
            raise RunnerError("validation images must be linux/arm64")
        if _LOCAL_IMAGE_ID.fullmatch(image) is not None:
            image_id = self._run(
                ["image", "inspect", "--format", "{{.Id}}", image],
                operation="local image identity inspection",
            ).stdout.strip()
            if image_id != digest:
                raise RunnerError("local image does not expose the requested content identity")
        else:
            raw_digests = self._run(
                ["image", "inspect", "--format", "{{json .RepoDigests}}", image],
                operation="image digest inspection",
            ).stdout
            try:
                repo_digests = json.loads(raw_digests)
            except json.JSONDecodeError as error:
                raise RunnerError("Docker image digest evidence is invalid") from error
            if not isinstance(repo_digests, list) or not any(
                isinstance(value, str) and value.endswith(f"@{digest}") for value in repo_digests
            ):
                raise RunnerError("pulled image does not expose the requested manifest digest")
        user = self._run(
            ["image", "inspect", "--format", "{{.Config.User}}", image],
            operation="image user inspection",
        ).stdout.strip()
        if user.split(":", maxsplit=1)[0] != str(_CONTAINER_UID):
            raise RunnerError("validation images must declare runtime UID 10001")
        revision = self._run(
            [
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                image,
            ],
            operation="image source revision inspection",
        ).stdout.strip()
        if revision != expected_source_commit:
            raise RunnerError("image source revision does not match its declared source commit")

    def run_detached(self, name: str, arguments: Sequence[str]) -> None:
        result = self._run(
            ["run", "--detach", "--name", name, *arguments],
            operation=f"{name} start",
        )
        if not result.stdout.strip():
            raise RunnerError(f"Docker {name} start returned no container identity")

    def state(self, name: str) -> dict[str, object]:
        raw_state = self._run(
            ["inspect", "--format", "{{json .State}}", name],
            operation=f"{name} state inspection",
        ).stdout
        restart_text = self._run(
            ["inspect", "--format", "{{.RestartCount}}", name],
            operation=f"{name} restart inspection",
        ).stdout.strip()
        try:
            state = dict(_mapping(json.loads(raw_state), f"{name} state"))
            state["RestartCount"] = int(restart_text)
        except (json.JSONDecodeError, ValueError) as error:
            raise RunnerError(f"Docker {name} state evidence is invalid") from error
        return state

    def pid(self, name: str) -> int:
        value = self._run(
            ["inspect", "--format", "{{.State.Pid}}", name],
            operation=f"{name} PID inspection",
        ).stdout.strip()
        try:
            result = int(value)
        except ValueError as error:
            raise RunnerError(f"Docker {name} host PID is invalid") from error
        if result <= 0:
            raise RunnerError(f"Docker {name} has no running host PID")
        return result

    def wait(self, name: str, *, timeout_s: float) -> int:
        value = self._run(
            ["wait", name], operation=f"{name} wait", timeout_s=timeout_s
        ).stdout.strip()
        try:
            return int(value)
        except ValueError as error:
            raise RunnerError(f"Docker {name} exit code is invalid") from error

    def stop(self, name: str) -> None:
        self._run(
            ["stop", "--time", "20", name],
            operation=f"{name} stop",
            capture=False,
            timeout_s=30,
        )

    def is_running(self, name: str) -> bool:
        result = self._run(
            ["inspect", "--format", "{{.State.Running}}", name],
            operation=f"{name} running inspection",
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def copy_from(self, name: str, source: str, destination: Path) -> bool:
        result = self._run(
            ["cp", f"{name}:{source}", str(destination)],
            operation=f"{name} artifact copy",
            check=False,
        )
        return result.returncode == 0

    def remove(self, name: str) -> None:
        self._run(["rm", "--force", name], operation=f"{name} removal", check=False)


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path
    config: Path
    sockets: Path
    generator_status: Path
    processing_status: Path
    clock: Path
    measurement: Path
    telemetry: Path
    helper_output: Path
    handoff: Path


def _make_paths(root: Path) -> Paths:
    if not root.is_absolute():
        raise RunnerError("output directory must be absolute")
    if root.exists() or root.is_symlink():
        raise RunnerError("output directory already exists")
    if any(character in str(root) for character in (":", "\n", "\r")):
        raise RunnerError("output directory contains unsupported characters")
    root.mkdir(mode=0o770, parents=True)
    root.chmod(0o770)
    runtime = root / "runtime"
    runtime.mkdir(mode=0o770)
    runtime.chmod(0o770)
    paths = Paths(
        root=root,
        config=root / "config",
        sockets=runtime / "sockets",
        generator_status=runtime / "generator-status",
        processing_status=runtime / "processing-status",
        clock=runtime / "clock",
        measurement=runtime / "measurement",
        telemetry=runtime / "telemetry",
        helper_output=runtime / "helper-output",
        handoff=root / "handoff",
    )
    for path in (
        paths.config,
        paths.sockets,
        paths.generator_status,
        paths.processing_status,
        paths.clock,
        paths.measurement,
        paths.telemetry,
        paths.helper_output,
        paths.handoff,
    ):
        path.mkdir(mode=0o770)
        path.chmod(0o770)
    return paths


def _copy_exclusive(source: Path, destination: Path) -> None:
    try:
        content = source.read_bytes()
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except (FileExistsError, OSError) as error:
        raise RunnerError(f"cannot stage public configuration {source.name}") from error


def _mount(source: Path, destination: str, *, readonly: bool = False) -> list[str]:
    value = f"{source}:{destination}"
    if readonly:
        value += ":ro"
    return ["--volume", value]


def _security_options(host_gid: int) -> list[str]:
    return [
        "--restart",
        "no",
        "--user",
        f"{_CONTAINER_UID}:{host_gid}",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=32m",
    ]


def _container_state_evidence(state: Mapping[str, object]) -> dict[str, object]:
    exit_code = state.get("ExitCode")
    oom_killed = state.get("OOMKilled")
    restart_count = state.get("RestartCount")
    if (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or not isinstance(oom_killed, bool)
        or isinstance(restart_count, bool)
        or not isinstance(restart_count, int)
    ):
        raise RunnerError("Docker lifecycle evidence is incomplete")
    return {
        "exit_code": exit_code,
        "oom_killed": oom_killed,
        "restart_count": restart_count,
    }


def _running_state_evidence(state: Mapping[str, object]) -> dict[str, object]:
    if state.get("Running") is not True:
        raise RunnerError("target container did not remain running during setup")
    result = _container_state_evidence(state)
    if result["restart_count"] != 0 or result["oom_killed"] is not False:
        raise RunnerError("target container lifecycle was not clean at setup")
    return result


def _validate_observation_evidence(
    document: Mapping[str, object],
    *,
    runtime_run_id: str,
    generator_source_commit: str,
    environment_id: str,
    seed: int,
) -> None:
    if set(document) != _OBSERVATION_RESULT_FIELDS:
        raise RunnerError("observation evidence fields differ from the contract")
    exact_values = {
        "schema_version": "long-validation-observation-result.v1",
        "run_id": runtime_run_id,
        "generator_source_commit": generator_source_commit,
        "environment_id": environment_id,
        "seed": seed,
        "received_records": _EXPECTED_OBSERVATION_RECORDS,
        "first_sequence": 1,
        "last_sequence": _EXPECTED_OBSERVATION_RECORDS,
        "surface_change_count": _EXPECTED_OBSERVATION_RECORDS - 1,
    }
    if any(document.get(name) != expected for name, expected in exact_values.items()):
        raise RunnerError("observation evidence identity or count is invalid")
    if any(
        isinstance(document.get(name), bool) or not isinstance(document.get(name), int)
        for name in ("seed", "received_records", "first_sequence", "last_sequence")
    ):
        raise RunnerError("observation evidence integer fields are invalid")
    for name, expected in (("first_elapsed_s", 0.1), ("last_elapsed_s", 3_900.0)):
        value = document.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not math.isclose(float(value), expected, rel_tol=1e-12, abs_tol=1e-9)
        ):
            raise RunnerError("observation evidence time boundaries are invalid")
    for name in (
        "input_fingerprint_sha256",
        "scene_fingerprint_sha256",
        "surface_stream_sha256",
    ):
        value = document.get(name)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise RunnerError("observation fingerprint evidence is invalid")
    minimum_volume = document.get("minimum_surface_volume_m3")
    maximum_volume = document.get("maximum_surface_volume_m3")
    if (
        isinstance(minimum_volume, bool)
        or not isinstance(minimum_volume, (int, float))
        or not math.isfinite(float(minimum_volume))
        or float(minimum_volume) < 0.0
        or isinstance(maximum_volume, bool)
        or not isinstance(maximum_volume, (int, float))
        or not math.isfinite(float(maximum_volume))
        or float(maximum_volume) <= float(minimum_volume)
    ):
        raise RunnerError("observation surface volume evidence is invalid")


def _read_helper_ready(docker: Docker, helper_name: str, output_root: Path) -> int:
    deadline = time.monotonic() + 10.0
    with tempfile.TemporaryDirectory(prefix="long-validation-ready-") as temporary:
        target = Path(temporary) / "ready.json"
        while time.monotonic() < deadline:
            if docker.copy_from(helper_name, "/validation/handoff/helper-ready.json", target):
                ready = _load_json(target)
                if (
                    set(ready)
                    != {
                        "schema_version",
                        "ready_at_monotonic_ns",
                        "start_at_monotonic_ns",
                    }
                    or ready.get("schema_version") != "long-validation-helper-ready.v1"
                ):
                    raise RunnerError("helper ready evidence has an unexpected contract")
                start = ready.get("start_at_monotonic_ns")
                if (
                    isinstance(start, bool)
                    or not isinstance(start, int)
                    or start <= time.monotonic_ns()
                ):
                    raise RunnerError("helper shared monotonic start is invalid")
                _atomic_json(output_root / "helper-ready.json", ready)
                return start
            if not docker.is_running(helper_name):
                raise RunnerError("helper exited before publishing readiness")
            time.sleep(0.1)
    raise RunnerError("helper did not publish readiness within 10 seconds")


def _get_throttled() -> int:
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise RunnerError("vcgencmd is unavailable") from error
    if result.returncode != 0:
        raise RunnerError("vcgencmd get_throttled failed")
    return parse_throttled(result.stdout)


def _device(docker_version: str, cooling: str, temperature_path: Path) -> dict[str, object]:
    model_path = Path("/proc/device-tree/model")
    try:
        model = model_path.read_bytes().rstrip(b"\x00").decode("ascii")
        os_release = platform.freedesktop_os_release()["PRETTY_NAME"]
        governor_values = {
            path.read_text(encoding="ascii").strip()
            for path in Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_governor")
        }
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except (KeyError, OSError, UnicodeError, ValueError) as error:
        raise RunnerError("device provenance is unavailable") from error
    if not model.startswith("Raspberry Pi 5 Model B"):
        raise RunnerError("long validation requires a Raspberry Pi 5 Model B")
    architecture = platform.machine()
    _validate_architecture(architecture)
    if len(governor_values) != 1:
        raise RunnerError("one CPU governor value is required")
    if not temperature_path.is_absolute() or not temperature_path.is_file():
        raise RunnerError("device temperature evidence is unavailable")
    logical_cpu_count = os.cpu_count()
    try:
        effective_cpu_count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError) as error:
        raise RunnerError("effective CPU affinity is unavailable") from error
    if logical_cpu_count is None:
        raise RunnerError("logical CPU count is unavailable")
    memory_bytes = page_size * page_count
    _validate_device_capacity(logical_cpu_count, effective_cpu_count, memory_bytes)
    return {
        "model": model,
        "architecture": architecture,
        "os_release": os_release,
        "kernel": platform.release(),
        "docker_version": docker_version,
        "logical_cpu_count": logical_cpu_count,
        "effective_cpu_count": effective_cpu_count,
        "memory_bytes": memory_bytes,
        "cpu_governor": governor_values.pop(),
        "cooling": cooling,
    }


def _validate_device_capacity(
    logical_cpu_count: int, effective_cpu_count: int, memory_bytes: int
) -> None:
    if logical_cpu_count != _EXPECTED_LOGICAL_CPU_COUNT:
        raise RunnerError("long validation requires four logical Raspberry Pi CPUs")
    if effective_cpu_count != _EXPECTED_LOGICAL_CPU_COUNT:
        raise RunnerError("long validation requires affinity to all four Raspberry Pi CPUs")
    if not _MIN_PI5_8GB_MEMORY_BYTES <= memory_bytes < _MAX_PI5_8GB_MEMORY_BYTES:
        raise RunnerError("long validation requires the Raspberry Pi 5 8 GB memory range")


def _validate_architecture(architecture: str) -> None:
    if architecture != "aarch64":
        raise RunnerError("long validation requires a native aarch64 operating system")


class CaseRunner:
    def __init__(self, arguments: argparse.Namespace, docker: Docker) -> None:
        self.arguments = arguments
        self.docker = docker
        self.container_names: list[str] = []
        suffix = f"{arguments.run_id}-{os.getpid()}"
        self.generator_name = f"sm-lv-generator-{suffix}"
        self.processing_name = f"sm-lv-processing-{suffix}"
        self.helper_name = f"sm-lv-helper-{suffix}"

    def cleanup(self) -> None:
        for name in reversed(self.container_names):
            if self.docker.is_running(name):
                with suppress(RunnerError):
                    self.docker.stop(name)
            self.docker.remove(name)

    def run(self) -> int:
        configuration = load_public_configuration(self.arguments.repository)
        source_archive_sha256 = verify_repository_archive(
            self.arguments.repository,
            self.arguments.source_archive,
            self.arguments.generator_source_commit,
        )
        if (
            self.arguments.processing_source_commit
            != configuration.validation_processing_source_commit
        ):
            raise RunnerError("processing source commit differs from the validation image source")
        initial_throttled = _get_throttled()
        if initial_throttled != 0:
            raise RunnerError("thermal throttle history must be 0x0 before validation")
        self.docker.pull(self.arguments.generator_image)
        self.docker.verify_image(
            self.arguments.generator_image, self.arguments.generator_source_commit
        )
        if _DIGEST_REFERENCE.fullmatch(self.arguments.processing_image) is not None:
            self.docker.pull(self.arguments.processing_image)
        self.docker.verify_image(
            self.arguments.processing_image,
            self.arguments.processing_source_commit,
            allow_local_id=True,
        )
        device = _device(
            self.docker.version(), self.arguments.cooling, self.arguments.temperature_path
        )
        paths = _make_paths(self.arguments.output_dir)
        for source in configuration.source_paths:
            _copy_exclusive(source, paths.config / source.name)
        if source_archive_sha256 != verify_repository_archive(
            self.arguments.repository,
            self.arguments.source_archive,
            self.arguments.generator_source_commit,
        ):
            raise RunnerError("source archive changed during validation setup")
        configuration = _load_staged_public_configuration(
            paths.config,
            configuration.pinned_processing_source_commit,
            configuration.validation_processing_source_commit,
        )
        try:
            return self._run_containers(
                configuration,
                device,
                paths,
                initial_throttled,
            )
        finally:
            self.cleanup()

    def _run_containers(
        self,
        configuration: PublicConfiguration,
        device: Mapping[str, object],
        paths: Paths,
        initial_throttled: int,
    ) -> int:
        host_gid = os.getgid()
        self._start_helper(paths, host_gid)
        start_ns = _read_helper_ready(self.docker, self.helper_name, paths.root)
        self._start_processing(configuration, paths, host_gid)
        self._start_generator(configuration, paths, host_gid, start_ns)
        generator_pid = self.docker.pid(self.generator_name)
        processing_pid = self.docker.pid(self.processing_name)
        helper_pid = self.docker.pid(self.helper_name)
        generator_host_cgroup, generator_cgroup = parse_cgroup_path(
            Path("/proc"), Path("/sys/fs/cgroup"), generator_pid
        )
        processing_host_cgroup, processing_cgroup = parse_cgroup_path(
            Path("/proc"), Path("/sys/fs/cgroup"), processing_pid
        )
        helper_host_cgroup, _ = parse_cgroup_path(Path("/proc"), Path("/sys/fs/cgroup"), helper_pid)
        device = dict(device)
        device["runtime_constraints"] = [
            _read_effective_constraints("generator", generator_host_cgroup, Path("/sys/fs/cgroup")),
            _read_effective_constraints(
                "processing", processing_host_cgroup, Path("/sys/fs/cgroup")
            ),
            _read_effective_constraints("helper", helper_host_cgroup, Path("/sys/fs/cgroup")),
        ]
        initial_states = {
            "generator": _running_state_evidence(self.docker.state(self.generator_name)),
            "processing": _running_state_evidence(self.docker.state(self.processing_name)),
        }
        self._write_control(
            configuration,
            device,
            paths,
            start_ns,
            generator_pid,
            processing_pid,
            generator_cgroup,
            processing_cgroup,
        )
        generator_wait_code = self.docker.wait(
            self.generator_name, timeout_s=_GENERATOR_WAIT_TIMEOUT_S
        )
        time.sleep(2.0)
        self.docker.stop(self.processing_name)
        processing_wait_code = self.docker.wait(
            self.processing_name, timeout_s=_PROCESSING_WAIT_TIMEOUT_S
        )
        final_throttled = _get_throttled()
        final_states = {
            "generator": _container_state_evidence(self.docker.state(self.generator_name)),
            "processing": _container_state_evidence(self.docker.state(self.processing_name)),
        }
        final_states["generator"]["exit_code"] = generator_wait_code
        final_states["processing"]["exit_code"] = processing_wait_code
        lifecycle, evidence = derive_lifecycle(
            run_id=self.arguments.run_id,
            initial_throttled=initial_throttled,
            final_throttled=final_throttled,
            initial_states=initial_states,
            final_states=final_states,
        )
        _atomic_json(paths.root / "lifecycle-evidence.json", evidence)
        _atomic_json(paths.handoff / "lifecycle-result.json", lifecycle)
        if generator_wait_code != 0 or processing_wait_code != 0:
            raise RunnerError("target container did not stop cleanly")
        if self.arguments.observation_mode == "actual":
            self._complete_observation_handoff(configuration, paths)
        helper_exit = self.docker.wait(self.helper_name, timeout_s=_HELPER_WAIT_TIMEOUT_S)
        result_target = paths.root / "run-result.v1.json"
        with tempfile.TemporaryDirectory(prefix="long-validation-result-") as temporary:
            copied = Path(temporary) / "run-result.v1.json"
            if not self.docker.copy_from(
                self.helper_name,
                "/validation/runtime/helper-output/run-result.v1.json",
                copied,
            ):
                raise RunnerError("helper did not produce an aggregate run result")
            _copy_exclusive(copied, result_target)
        return helper_exit if helper_exit in (0, 1) else 2

    def _start_helper(self, paths: Paths, host_gid: int) -> None:
        arguments = [
            *_security_options(host_gid),
            "--network",
            "none",
            "--pid",
            "host",
            "--cgroupns",
            "host",
            *_mount(self.arguments.repository, "/validation-repo", readonly=True),
            *_mount(paths.root, "/validation"),
            *_mount(Path("/proc"), "/host/proc", readonly=True),
            *_mount(Path("/sys"), "/host/sys", readonly=True),
            "--workdir",
            "/validation-repo",
            "--entrypoint",
            "/app/.venv/bin/python",
            self.arguments.processing_image,
            "-m",
            "tests.edge.long_validation.helper",
            "--control",
            "/validation/handoff/control.json",
            "--measurement-socket",
            "/validation/runtime/measurement/measurement.sock",
            "--generator-status-dir",
            "/validation/runtime/generator-status",
            "--processing-status-dir",
            "/validation/runtime/processing-status",
            "--clock-file",
            "/validation/runtime/clock/clock.json",
            "--ready-file",
            "/validation/handoff/helper-ready.json",
            "--output",
            "/validation/runtime/helper-output/run-result.v1.json",
            "--proc-root",
            "/host/proc",
            "--cgroup-root",
            "/host/sys/fs/cgroup",
            "--warmup-s",
            str(_WARMUP_S),
            "--measure-s",
            str(_MEASUREMENT_S),
            "--setup-lead-s",
            "30",
            "--control-timeout-s",
            "20",
            "--final-result-timeout-s",
            str(self.arguments.handoff_timeout_s + 30),
        ]
        self.docker.run_detached(self.helper_name, arguments)
        self.container_names.append(self.helper_name)

    def _start_processing(
        self,
        configuration: PublicConfiguration,
        paths: Paths,
        host_gid: int,
    ) -> None:
        config_sha256 = hashlib.sha256(configuration.source_paths[3].read_bytes()).hexdigest()
        arguments = [
            *_security_options(host_gid),
            "--network",
            "none",
            "--env",
            f"CONFIG_SHA256={config_sha256}",
            "--env",
            f"SITE_ID={configuration.site_id}",
            "--env",
            f"EDGE_ID={configuration.edge_id}",
            "--env",
            f"CONFIG_REVISION={configuration.config_revision}",
            *_mount(paths.config, "/config", readonly=True),
            *_mount(paths.sockets, "/sockets"),
            *_mount(paths.processing_status, "/status"),
            *_mount(paths.clock, "/clock", readonly=True),
            *_mount(paths.measurement, "/uplink"),
            self.arguments.processing_image,
            "--config",
            "/config/processing.synthetic.json",
            "--status-dir",
            "/status",
            "--clock-file",
            "/clock/clock.json",
            "--uplink",
            "unix:/uplink/measurement.sock",
        ]
        self.docker.run_detached(self.processing_name, arguments)
        self.container_names.append(self.processing_name)

    def _start_generator(
        self,
        configuration: PublicConfiguration,
        paths: Paths,
        host_gid: int,
        start_ns: int,
    ) -> None:
        observation_mode = "actual" if self.arguments.observation_mode == "actual" else "no-op"
        observation_host = (
            self.arguments.observation_host
            if self.arguments.observation_mode == "actual"
            else "127.0.0.1"
        )
        observation_port = self.arguments.observation_port if observation_mode == "actual" else 9
        network = "bridge" if observation_mode == "actual" else "none"
        arguments = [
            *_security_options(host_gid),
            "--network",
            network,
            *_mount(paths.config, "/config", readonly=True),
            *_mount(paths.sockets, "/sockets"),
            *_mount(paths.generator_status, "/status"),
            *_mount(paths.telemetry, "/telemetry"),
            self.arguments.generator_image,
            "edge-validation",
            "--config",
            "/config/generator.v2.json",
            "--grpc-socket-dir",
            "/sockets",
            "--status-dir",
            "/status",
            "--site-id",
            configuration.site_id,
            "--edge-id",
            configuration.edge_id,
            "--config-revision",
            configuration.config_revision,
            "--deployment-revision",
            f"edge-validation-{self.arguments.run_id}",
            "--observation-host",
            observation_host,
            "--observation-port",
            str(observation_port),
            "--observation-interval-s",
            "1",
            "--diagnostics-enabled",
            "false",
            "--mean-fill-duration-s",
            str(self.arguments.mean_fill_duration_s),
            "--observation-mode",
            observation_mode,
            "--warmup-duration-s",
            str(_WARMUP_S),
            "--measurement-duration-s",
            str(_MEASUREMENT_S),
            "--max-samples",
            str(_MAX_SAMPLES),
            "--start-at-monotonic-ns",
            str(start_ns),
            "--output",
            "/telemetry/runtime-telemetry.json",
        ]
        self.docker.run_detached(self.generator_name, arguments)
        self.container_names.append(self.generator_name)

    def _write_control(
        self,
        configuration: PublicConfiguration,
        device: Mapping[str, object],
        paths: Paths,
        start_ns: int,
        generator_pid: int,
        processing_pid: int,
        generator_cgroup: Path,
        processing_cgroup: Path,
    ) -> None:
        generator_paths = configuration.source_paths[:3]
        control: dict[str, object] = {
            "schema_version": "long-validation-control.v1",
            "start_at_monotonic_ns": start_ns,
            "identity": {
                "generator_source_commit": self.arguments.generator_source_commit,
                "generator_image_digest": parse_digest_reference(self.arguments.generator_image),
                "processing_source_commit": self.arguments.processing_source_commit,
                "processing_image_digest": parse_processing_image_reference(
                    self.arguments.processing_image
                ),
                "config_fingerprint_sha256": fingerprint_files(generator_paths),
                "processing_config_sha256": hashlib.sha256(
                    configuration.source_paths[3].read_bytes()
                ).hexdigest(),
                "seed": configuration.seed,
            },
            "expected_measurement_identity": {
                "site_id": configuration.site_id,
                "edge_id": configuration.edge_id,
                "config_revision": configuration.config_revision,
                "calibration_version": configuration.calibration_version,
            },
            "device": dict(device),
            "workload": {
                "mean_fill_duration_s": self.arguments.mean_fill_duration_s,
                "observation_mode": self.arguments.observation_mode,
            },
            "generator": {
                "host_pid": generator_pid,
                "cgroup_path": str(generator_cgroup),
            },
            "processing": {
                "host_pid": processing_pid,
                "cgroup_path": str(processing_cgroup),
            },
            "runtime_telemetry_path": "/validation/runtime/telemetry/runtime-telemetry.json",
            "temperature_path": f"/host{self.arguments.temperature_path}",
            "observation_result_path": (
                "/validation/handoff/observation-result.json"
                if self.arguments.observation_mode == "actual"
                else None
            ),
            "lifecycle_result_path": "/validation/handoff/lifecycle-result.json",
        }
        _atomic_json(paths.handoff / "control.json", control)

    def _complete_observation_handoff(
        self, configuration: PublicConfiguration, paths: Paths
    ) -> None:
        staged = paths.handoff / "observation-result.staged.json"
        final = paths.handoff / "observation-result.json"
        if staged.exists() or staged.is_symlink() or final.exists() or final.is_symlink():
            raise RunnerError("observation evidence appeared before the handoff request")
        marker = {
            "schema_version": "long-validation-observation-handoff-request.v1",
            "run_id": self.arguments.run_id,
        }
        _atomic_json(paths.handoff / "observation-handoff-request.json", marker)
        telemetry = _load_json(paths.telemetry / "runtime-telemetry.json")
        runtime_run_id = telemetry.get("run_id")
        if not isinstance(runtime_run_id, str) or not runtime_run_id:
            raise RunnerError("runtime telemetry has an invalid observation run identity")
        deadline = time.monotonic() + self.arguments.handoff_timeout_s
        while time.monotonic() < deadline:
            if staged.is_file():
                if staged.is_symlink():
                    raise RunnerError("observation evidence must be a regular staged file")
                document = _load_json(staged)
                _validate_observation_evidence(
                    document,
                    runtime_run_id=runtime_run_id,
                    generator_source_commit=self.arguments.generator_source_commit,
                    environment_id=configuration.environment_id,
                    seed=configuration.seed,
                )
                staged.chmod(0o644)
                try:
                    os.link(staged, final)
                except FileExistsError as error:
                    raise RunnerError("observation evidence destination already exists") from error
                staged.unlink()
                return
            if not self.docker.is_running(self.helper_name):
                raise RunnerError("helper exited while waiting for observation evidence")
            time.sleep(0.2)
        raise RunnerError("observation evidence handoff timed out")


def _validate_arguments(arguments: argparse.Namespace) -> None:
    parse_digest_reference(arguments.generator_image)
    parse_processing_image_reference(arguments.processing_image)
    for value in (arguments.generator_source_commit, arguments.processing_source_commit):
        if _SOURCE_COMMIT.fullmatch(value) is None:
            raise RunnerError("source commits must be full lowercase 40-character SHAs")
    if _RUN_ID.fullmatch(arguments.run_id) is None:
        raise RunnerError("run ID must be a bounded lowercase filesystem-safe identifier")
    if arguments.observation_mode == "actual" and not arguments.observation_host:
        raise RunnerError("actual observation requires --observation-host")
    if arguments.observation_mode == "noop" and arguments.observation_host is not None:
        raise RunnerError("noop observation must not provide --observation-host")
    if not arguments.cooling or any(character in arguments.cooling for character in "\r\n"):
        raise RunnerError("cooling description must be one non-empty line")
    _validate_repository_paths(arguments.repository, arguments.output_dir, arguments.source_archive)
    if not arguments.temperature_path.is_absolute() or not str(
        arguments.temperature_path
    ).startswith("/sys/"):
        raise RunnerError("temperature path must be an absolute /sys path")


def _validate_repository_paths(repository: Path, output_dir: Path, source_archive: Path) -> None:
    if not repository.is_absolute() or not repository.is_dir():
        raise RunnerError("repository path must be an existing absolute directory")
    if output_dir.resolve().is_relative_to(repository.resolve()):
        raise RunnerError("output directory must be outside the validation repository")
    if not source_archive.is_absolute() or not source_archive.is_file():
        raise RunnerError("source archive must be an existing absolute file")
    if source_archive.resolve().is_relative_to(repository.resolve()):
        raise RunnerError("source archive must be outside the validation repository")


def _port(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65_535:
        raise argparse.ArgumentTypeError("port must be from 1 through 65535")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    repository = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Run one fixed 300-second warmup and 3600-second Raspberry Pi validation.",
        epilog=(
            "Actual mode requires an already-ready external receiver. When "
            "handoff/observation-handoff-request.json appears, stop that receiver and atomically "
            "place its result at handoff/observation-result.staged.json."
        ),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--generator-image", required=True)
    parser.add_argument("--generator-source-commit", required=True)
    parser.add_argument("--source-archive", required=True, type=Path)
    parser.add_argument("--processing-image", required=True)
    parser.add_argument("--processing-source-commit", required=True)
    parser.add_argument("--mean-fill-duration-s", required=True, type=int, choices=(600, 86_400))
    parser.add_argument("--observation-mode", required=True, choices=("actual", "noop"))
    parser.add_argument(
        "--observation-host", help="Routable external receiver host for actual mode"
    )
    parser.add_argument("--observation-port", type=_port, default=17_000)
    parser.add_argument("--cooling", required=True)
    parser.add_argument("--handoff-timeout-s", type=_positive_int, default=300)
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument(
        "--temperature-path",
        type=Path,
        default=Path("/sys/class/thermal/thermal_zone0/temp"),
    )
    parser.add_argument("--repository", type=Path, default=repository, help=argparse.SUPPRESS)
    return parser


def _raise_interrupt(_signal_number: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        _validate_arguments(arguments)
        executable = shutil.which(arguments.docker_command)
        if executable is None:
            raise RunnerError("configured Docker command is unavailable")
        signal.signal(signal.SIGTERM, _raise_interrupt)
        return CaseRunner(arguments, Docker(executable)).run()
    except KeyboardInterrupt:
        print("long validation interrupted", file=sys.stderr)
        return 130
    except (OSError, RunnerError) as error:
        print(f"long validation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
