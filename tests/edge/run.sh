#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --image <digest-ref> --config-dir <path> [--duration-s <seconds>] [--cpus <count>] [--output-dir <path>]" >&2
}

image_ref=""
config_dir=""
duration_s="30"
cpu_limit="2"
output_dir=""

while (($# > 0)); do
  case "$1" in
    --image)
      (($# >= 2)) || { usage; exit 2; }
      image_ref="${2:-}"
      shift 2
      ;;
    --config-dir)
      (($# >= 2)) || { usage; exit 2; }
      config_dir="${2:-}"
      shift 2
      ;;
    --duration-s)
      (($# >= 2)) || { usage; exit 2; }
      duration_s="${2:-}"
      shift 2
      ;;
    --cpus)
      (($# >= 2)) || { usage; exit 2; }
      cpu_limit="${2:-}"
      shift 2
      ;;
    --output-dir)
      (($# >= 2)) || { usage; exit 2; }
      output_dir="${2:-}"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ ! "$image_ref" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]] || [[ -z "$config_dir" ]]; then
  usage
  exit 2
fi
if [[ ! "$duration_s" =~ ^[1-9][0-9]*$ ]]; then
  echo "duration must be a positive integer" >&2
  exit 2
fi
if [[ ! "$cpu_limit" =~ ^([1-9][0-9]*(\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$ ]]; then
  echo "CPU limit must be a positive number" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_dir="$(cd "$config_dir" && pwd)"
for required_file in environment.v1.json generator.v1.json quality-profile.v1.json; do
  if [[ ! -f "$config_dir/$required_file" ]]; then
    echo "missing configuration file: $config_dir/$required_file" >&2
    exit 2
  fi
done

if [[ -z "$output_dir" ]]; then
  output_dir="$(mktemp -d -t lidar-edge-validation.XXXXXX)"
else
  mkdir -p "$output_dir"
  output_dir="$(cd "$output_dir" && pwd)"
fi
for result_file in docker-stats.jsonl generator.log receiver.log container-inspect.json; do
  if [[ -e "$output_dir/$result_file" ]]; then
    echo "result file already exists: $output_dir/$result_file" >&2
    exit 2
  fi
done

resource_prefix="lidar-validation-$(date +%s)-$$"
network_name="$resource_prefix"
receiver_name="$resource_prefix-receiver"
generator_name="$resource_prefix-generator"

cleanup() {
  docker container rm --force "$generator_name" >/dev/null 2>&1 || true
  docker container rm --force "$receiver_name" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker image pull "$image_ref"
docker network create "$network_name" >/dev/null
docker run --detach \
  --name "$receiver_name" \
  --network "$network_name" \
  --network-alias receiver \
  --mount "type=bind,src=$config_dir,dst=/config,readonly" \
  --mount "type=bind,src=$script_dir,dst=/validation,readonly" \
  --entrypoint /app/.venv/bin/python \
  "$image_ref" \
  /validation/receiver.py --environment /config/environment.v1.json >/dev/null

receiver_ready="false"
for _ in {1..100}; do
  if docker logs "$receiver_name" 2>&1 | grep -q '^validation_receiver_ready='; then
    receiver_ready="true"
    break
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "$receiver_name")" != "true" ]]; then
    break
  fi
  sleep 0.1
done
if [[ "$receiver_ready" != "true" ]]; then
  docker logs "$receiver_name" >&2
  echo "validation receiver did not become ready" >&2
  exit 1
fi

docker run --detach \
  --name "$generator_name" \
  --network "$network_name" \
  --cpus "$cpu_limit" \
  --mount "type=bind,src=$config_dir/environment.v1.json,dst=/config/environment.v1.json,readonly" \
  --mount "type=bind,src=$config_dir/generator.v1.json,dst=/config/generator.v1.json,readonly" \
  --mount "type=bind,src=$config_dir/quality-profile.v1.json,dst=/config/quality-profile.v1.json,readonly" \
  --tmpfs /config/diagnostics:uid=10001,gid=10001,mode=0700 \
  "$image_ref" \
  --config /config/generator.v1.json \
  --observation-host receiver \
  --observation-port 9100 >/dev/null

sleep "$duration_s"
docker stats --no-stream --format '{{json .}}' "$generator_name" "$receiver_name" \
  > "$output_dir/docker-stats.jsonl"
docker stop --time 10 "$generator_name" >/dev/null
sleep 1
docker stop --time 10 "$receiver_name" >/dev/null
docker logs "$generator_name" > "$output_dir/generator.log" 2>&1
docker logs "$receiver_name" > "$output_dir/receiver.log" 2>&1
docker inspect "$generator_name" "$receiver_name" > "$output_dir/container-inspect.json"
generator_exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$generator_name")"
receiver_exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$receiver_name")"
if [[ "$generator_exit_code" != "0" || "$receiver_exit_code" != "0" ]]; then
  echo "validation container exit code: generator=$generator_exit_code receiver=$receiver_exit_code" >&2
  echo "validation_output=$output_dir"
  exit 1
fi

if ! docker run --rm \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$config_dir,dst=/config,readonly" \
  --mount "type=bind,src=$script_dir,dst=/validation,readonly" \
  --mount "type=bind,src=$output_dir,dst=/results,readonly" \
  --entrypoint /app/.venv/bin/python \
  "$image_ref" \
  /validation/check_result.py \
  --environment /config/environment.v1.json \
  --generator-log /results/generator.log \
  --receiver-log /results/receiver.log \
  --max-pending-frames 2; then
  echo "validation_output=$output_dir"
  exit 1
fi

echo "validation_output=$output_dir"
