import argparse
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from .run_case import (
    Docker,
    RunnerError,
    _atomic_json,
    _copy_exclusive,
    _load_staged_public_configuration,
    _make_paths,
    _read_effective_constraints,
    _validate_architecture,
    _validate_arguments,
    _validate_device_capacity,
    derive_lifecycle,
    fingerprint_files,
    load_public_configuration,
    parse_cgroup_path,
    parse_digest_reference,
    parse_processing_image_reference,
    parse_throttled,
    verify_repository_archive,
)


class _FakeDocker(Docker):
    def __init__(self, responses: dict[tuple[str, ...], str]) -> None:
        super().__init__("fake-docker")
        self.responses = responses

    def _run(
        self,
        arguments: Sequence[str],
        *,
        operation: str,
        capture: bool = True,
        check: bool = True,
        timeout_s: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del operation, capture, check, timeout_s
        key = tuple(arguments)
        if key not in self.responses:
            return subprocess.CompletedProcess([self.executable, *arguments], 1, "", "")
        return subprocess.CompletedProcess(
            [self.executable, *arguments], 0, self.responses[key], ""
        )


def _image() -> str:
    return f"ghcr.io/example/simulator@sha256:{'a' * 64}"


def test_digest_and_throttle_parsers_fail_closed() -> None:
    assert parse_digest_reference(_image()) == f"sha256:{'a' * 64}"
    assert parse_processing_image_reference(f"sha256:{'b' * 64}") == f"sha256:{'b' * 64}"
    assert parse_throttled("throttled=0x0\n") == 0
    assert parse_throttled("throttled=0x50000") == 0x50000
    with pytest.raises(RunnerError, match="digest references"):
        parse_digest_reference("ghcr.io/example/simulator:latest")
    with pytest.raises(RunnerError, match="digest references"):
        parse_processing_image_reference("ajin-lidar-processing:validation")
    with pytest.raises(RunnerError, match="unexpected"):
        parse_throttled("0")


def test_device_capacity_requires_exact_pi5_cpu_memory_and_architecture() -> None:
    _validate_device_capacity(4, 4, 7 * 1_073_741_824)
    _validate_device_capacity(4, 4, 9 * 1_073_741_824 - 1)
    _validate_architecture("aarch64")

    for logical, effective, memory in (
        (8, 4, 8 * 1_073_741_824),
        (4, 3, 8 * 1_073_741_824),
        (4, 4, 7 * 1_073_741_824 - 1),
        (4, 4, 9 * 1_073_741_824),
    ):
        with pytest.raises(RunnerError):
            _validate_device_capacity(logical, effective, memory)
    with pytest.raises(RunnerError, match="aarch64"):
        _validate_architecture("armv7l")


def test_effective_constraints_include_ancestor_limits_and_exact_cpuset(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cgroup"
    child = root / "container"
    child.mkdir(parents=True)
    (root / "cpu.max").write_text("max 100000\n", encoding="ascii")
    (root / "memory.max").write_text("max\n", encoding="ascii")
    (child / "cpu.max").write_text("400000 100000\n", encoding="ascii")
    (child / "memory.max").write_text(str(8 * 1_073_741_824), encoding="ascii")
    (child / "cpuset.cpus.effective").write_text("0-3\n", encoding="ascii")

    assert _read_effective_constraints("helper", child, root) == {
        "component": "helper",
        "effective_cpu_count": 4,
        "cpu_quota_cores": 4.0,
        "memory_limit_bytes": 8 * 1_073_741_824,
    }

    (root / "cpu.max").write_text("300000 100000\n", encoding="ascii")
    with pytest.raises(RunnerError, match="below four cores"):
        _read_effective_constraints("helper", child, root)


def test_fingerprint_is_path_independent_and_length_delimited(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_bytes(b"ab")
    second.write_bytes(b"c")
    digest = hashlib.sha256()
    for path in (first, second):
        name = path.name.encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    assert fingerprint_files((second, first)) == digest.hexdigest()


def test_public_configuration_has_two_exact_sensor_lanes() -> None:
    repository = Path(__file__).resolve().parents[3]
    configuration = load_public_configuration(repository)
    assert configuration.seed == 123456789
    assert configuration.site_id == "synthetic-site"
    assert configuration.calibration_version == "synthetic-scrap-pit-v1"
    assert configuration.pinned_processing_source_commit == (
        "666ca6067a3bb86833b74140cb659049025d0dae"
    )
    assert configuration.validation_processing_source_commit == (
        "55b2e9d9401682c237a42945d9f548a4c912951f"
    )
    assert [path.name for path in configuration.source_paths] == [
        "generator.v2.json",
        "environment.v1.json",
        "quality-profile.v1.json",
        "processing.synthetic.json",
    ]


def test_staged_configuration_becomes_the_runtime_provenance_source(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    source = load_public_configuration(repository)
    for path in source.source_paths:
        _copy_exclusive(path, tmp_path / path.name)

    staged = _load_staged_public_configuration(
        tmp_path,
        source.pinned_processing_source_commit,
        source.validation_processing_source_commit,
    )

    assert all(path.parent == tmp_path for path in staged.source_paths)
    assert fingerprint_files(staged.source_paths[:3]) == fingerprint_files(source.source_paths[:3])


def test_cgroup_parser_requires_one_real_v2_membership(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    process = proc_root / "42"
    target = cgroup_root / "system.slice" / "docker-test.scope"
    process.mkdir(parents=True)
    target.mkdir(parents=True)
    (process / "cgroup").write_text("0::/system.slice/docker-test.scope\n", encoding="ascii")
    (target / "cpu.stat").write_text("usage_usec 1\n", encoding="ascii")
    host_path, helper_path = parse_cgroup_path(proc_root, cgroup_root, 42)
    assert host_path == target
    assert helper_path == Path("/host/sys/fs/cgroup/system.slice/docker-test.scope")


def test_effective_constraints_allow_disabled_memory_controller(tmp_path: Path) -> None:
    root = tmp_path / "cgroup"
    child = root / "container"
    child.mkdir(parents=True)
    (child / "cpu.max").write_text("max 100000\n", encoding="ascii")
    (child / "cpuset.cpus.effective").write_text("0-3\n", encoding="ascii")

    assert _read_effective_constraints("helper", child, root) == {
        "component": "helper",
        "effective_cpu_count": 4,
        "cpu_quota_cores": None,
        "memory_limit_bytes": None,
    }


def test_lifecycle_counters_are_derived_from_explicit_evidence() -> None:
    initial = {
        component: {"restart_count": 0, "oom_killed": False, "exit_code": 0}
        for component in ("generator", "processing")
    }
    final = {
        "generator": {"restart_count": 0, "oom_killed": False, "exit_code": 0},
        "processing": {"restart_count": 1, "oom_killed": True, "exit_code": 137},
    }
    lifecycle, evidence = derive_lifecycle(
        run_id="test-run",
        initial_throttled=0,
        final_throttled=0x50000,
        initial_states=initial,
        final_states=final,
    )
    assert lifecycle == {
        "schema_version": "long-validation-lifecycle-result.v1",
        "container_restarts": 1,
        "oom_events": 1,
        "thermal_throttling_events": 1,
        "unclassified_loss_windows": 1,
    }
    assert evidence["thermal"] == {
        "get_throttled_after": "0x50000",
        "get_throttled_before": "0x0",
    }


def test_fake_docker_verifies_arm64_digest_and_declared_user() -> None:
    image = _image()
    source_commit = "b" * 40
    docker = _FakeDocker(
        {
            ("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image): ("linux/arm64\n"),
            (
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                image,
            ): json.dumps([image]),
            ("image", "inspect", "--format", "{{.Config.User}}", image): "10001:10001\n",
            (
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                image,
            ): f"{source_commit}\n",
        }
    )
    docker.verify_image(image, source_commit)


def test_fake_docker_verifies_local_processing_image_identity() -> None:
    image = f"sha256:{'d' * 64}"
    source_commit = "b" * 40
    docker = _FakeDocker(
        {
            ("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image): "linux/arm64\n",
            ("image", "inspect", "--format", "{{.Id}}", image): f"{image}\n",
            ("image", "inspect", "--format", "{{.Config.User}}", image): "10001:10001\n",
            (
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                image,
            ): f"{source_commit}\n",
        }
    )

    docker.verify_image(image, source_commit, allow_local_id=True)


def test_image_revision_must_match_the_reported_source_commit() -> None:
    image = _image()
    docker = _FakeDocker(
        {
            ("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image): ("linux/arm64\n"),
            (
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                image,
            ): json.dumps([image]),
            ("image", "inspect", "--format", "{{.Config.User}}", image): "10001:10001\n",
            (
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                image,
            ): f"{'b' * 40}\n",
        }
    )

    with pytest.raises(RunnerError, match="source revision"):
        docker.verify_image(image, "c" * 40)


def test_container_wait_uses_a_bounded_timeout() -> None:
    class TimeoutDocker(_FakeDocker):
        observed_timeout: float | None = None

        def _run(
            self,
            arguments: Sequence[str],
            *,
            operation: str,
            capture: bool = True,
            check: bool = True,
            timeout_s: float | None = None,
        ) -> subprocess.CompletedProcess[str]:
            self.observed_timeout = timeout_s
            return super()._run(
                arguments,
                operation=operation,
                capture=capture,
                check=check,
                timeout_s=timeout_s,
            )

    docker = TimeoutDocker({("wait", "container-a"): "0\n"})

    assert docker.wait("container-a", timeout_s=5.0) == 0
    assert docker.observed_timeout == 5.0


def test_docker_copy_stream_is_written_by_the_calling_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b'{"state":"ready"}\n'
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo("ready.json")
        member.size = len(payload)
        member.mode = 0o600
        archive.addfile(member, io.BytesIO(payload))

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        assert args[0] == ["sudo-docker", "cp", "helper:/ready.json", "-"]
        return subprocess.CompletedProcess(args[0], 0, stream.getvalue(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    destination = tmp_path / "ready.json"

    assert Docker("sudo-docker").copy_from("helper", "/ready.json", destination)
    assert destination.read_bytes() == payload
    assert destination.stat().st_uid == os.getuid()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_repository_archive_requires_exact_tree_and_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "tracked.txt").write_text("one\n", encoding="ascii")
    source_archive = tmp_path / "source.tar.gz"
    commit = "a" * 40
    with tarfile.open(
        source_archive,
        mode="w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": commit},
    ) as archive:
        archive.add(repository / "tracked.txt", arcname="tracked.txt")

    assert (
        verify_repository_archive(repository, source_archive, commit)
        == hashlib.sha256(source_archive.read_bytes()).hexdigest()
    )

    with pytest.raises(RunnerError, match="archive commit"):
        verify_repository_archive(repository, source_archive, "b" * 40)

    (repository / "untracked.txt").write_text("two\n", encoding="ascii")
    with pytest.raises(RunnerError, match="file set"):
        verify_repository_archive(repository, source_archive, commit)


def test_runtime_directories_are_group_writable_regardless_of_umask(tmp_path: Path) -> None:
    previous_umask = os.umask(0o077)
    try:
        paths = _make_paths(tmp_path / "case")
    finally:
        os.umask(previous_umask)

    directories = [
        paths.root,
        paths.root / "runtime",
        paths.config,
        paths.sockets,
        paths.generator_status,
        paths.processing_status,
        paths.clock,
        paths.measurement,
        paths.telemetry,
        paths.helper_output,
        paths.handoff,
    ]
    for path in directories:
        assert stat.S_IMODE(path.stat().st_mode) == 0o770


def test_staged_files_keep_declared_modes_regardless_of_umask(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("{}\n", encoding="ascii")
    copied = tmp_path / "copied.json"
    atomic = tmp_path / "atomic.json"
    previous_umask = os.umask(0o077)
    try:
        _copy_exclusive(source, copied)
        _atomic_json(atomic, {"value": 1})
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(copied.stat().st_mode) == 0o644
    assert stat.S_IMODE(atomic.stat().st_mode) == 0o644


def test_argument_boundary_separates_actual_and_noop_observation(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    source_archive = tmp_path / "source.tar.gz"
    source_archive.write_bytes(b"archive")
    common: dict[str, object] = {
        "run_id": "86400-actual-a",
        "output_dir": tmp_path / "new-output",
        "generator_image": _image(),
        "generator_source_commit": "a" * 40,
        "source_archive": source_archive,
        "processing_image": _image(),
        "processing_source_commit": "b" * 40,
        "mean_fill_duration_s": 86_400,
        "observation_mode": "actual",
        "observation_host": "receiver.example",
        "observation_port": 17_000,
        "cooling": "active cooler",
        "handoff_timeout_s": 300,
        "temperature_path": Path("/sys/class/thermal/thermal_zone0/temp"),
        "repository": repository,
    }
    _validate_arguments(argparse.Namespace(**common))
    common["observation_mode"] = "noop"
    with pytest.raises(RunnerError, match="must not provide"):
        _validate_arguments(argparse.Namespace(**common))
    common["observation_host"] = None
    _validate_arguments(argparse.Namespace(**common))
