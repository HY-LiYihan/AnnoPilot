#!/usr/bin/env bash
set -Eeuo pipefail

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

APP_DIR="${ANNOPILOT_APP_DIR:-/opt/annopilot}"
COMPOSE_FILE="${ANNOPILOT_COMPOSE_FILE:-$APP_DIR/compose.yml}"
DEPLOY_MODE="${ANNOPILOT_DEPLOY_MODE:-image}"
LOCK_FILE="${ANNOPILOT_DEPLOY_LOCK:-/tmp/annopilot-deploy.lock}"
HEALTH_URL="${ANNOPILOT_HEALTH_URL:-http://127.0.0.1:8888/api/health}"
HEALTH_RETRIES="${ANNOPILOT_HEALTH_RETRIES:-30}"
HEALTH_SLEEP_SECONDS="${ANNOPILOT_HEALTH_SLEEP_SECONDS:-2}"
DOCKER_COMMAND="${ANNOPILOT_DOCKER_COMMAND:-docker}"
RELEASE_DIR="${ANNOPILOT_RELEASE_DIR:-$APP_DIR/releases}"
CURRENT_RELEASE_FILE="$RELEASE_DIR/current.env"
LAST_SUCCESSFUL_FILE="$RELEASE_DIR/last-successful.env"
ROLLBACK_TARGET_FILE="$RELEASE_DIR/rollback-target.env"

IMAGE_TAG="${ANNOPILOT_IMAGE_TAG:-main}"
if [[ -z "$IMAGE_TAG" ]]; then
  IMAGE_TAG="main"
fi
export ANNOPILOT_IMAGE_TAG="$IMAGE_TAG"

read -r -a DOCKER_BIN <<< "$DOCKER_COMMAND"

docker_cmd() {
  "${DOCKER_BIN[@]}" "$@"
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    log "missing required file: $path"
    exit 1
  fi
}

healthcheck() {
  local attempt
  for attempt in $(seq 1 "$HEALTH_RETRIES"); do
    if curl -fsS "$HEALTH_URL" >/dev/null; then
      log "healthcheck passed: $HEALTH_URL"
      return 0
    fi
    log "healthcheck waiting ($attempt/$HEALTH_RETRIES): $HEALTH_URL"
    sleep "$HEALTH_SLEEP_SECONDS"
  done

  log "healthcheck failed: $HEALTH_URL"
  return 1
}

container_image_ref() {
  local container_name="$1"
  docker_cmd inspect --format '{{.Config.Image}}' "$container_name" 2>/dev/null || true
}

container_image_id() {
  local container_name="$1"
  docker_cmd inspect --format '{{.Image}}' "$container_name" 2>/dev/null || true
}

image_repo_digest() {
  local image_ref="$1"
  if [[ -z "$image_ref" ]]; then
    return 0
  fi
  docker_cmd image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$image_ref" 2>/dev/null || true
}

write_release_file() {
  local path="$1"
  local health_status="$2"
  local api_image web_image tmp_path
  api_image="$(container_image_ref annopilot-api)"
  web_image="$(container_image_ref annopilot-web)"
  tmp_path="${path}.tmp"
  {
    printf 'ANNOPILOT_IMAGE_TAG=%q\n' "$ANNOPILOT_IMAGE_TAG"
    printf 'ANNOPILOT_DEPLOY_SHA=%q\n' "${ANNOPILOT_DEPLOY_SHA:-}"
    printf 'ANNOPILOT_DEPLOY_REF=%q\n' "${ANNOPILOT_DEPLOY_REF:-}"
    printf 'ANNOPILOT_DEPLOYED_AT=%q\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    printf 'ANNOPILOT_HEALTH_STATUS=%q\n' "$health_status"
    printf 'ANNOPILOT_API_IMAGE=%q\n' "$api_image"
    printf 'ANNOPILOT_WEB_IMAGE=%q\n' "$web_image"
    printf 'ANNOPILOT_API_IMAGE_ID=%q\n' "$(container_image_id annopilot-api)"
    printf 'ANNOPILOT_WEB_IMAGE_ID=%q\n' "$(container_image_id annopilot-web)"
    printf 'ANNOPILOT_API_REPO_DIGEST=%q\n' "$(image_repo_digest "$api_image")"
    printf 'ANNOPILOT_WEB_REPO_DIGEST=%q\n' "$(image_repo_digest "$web_image")"
  } >"$tmp_path"
  mv "$tmp_path" "$path"
}

prepare_rollback_target() {
  rm -f "$ROLLBACK_TARGET_FILE"
  if [[ -f "$CURRENT_RELEASE_FILE" ]]; then
    cp "$CURRENT_RELEASE_FILE" "$ROLLBACK_TARGET_FILE"
  elif [[ -f "$LAST_SUCCESSFUL_FILE" ]]; then
    cp "$LAST_SUCCESSFUL_FILE" "$ROLLBACK_TARGET_FILE"
  fi
}

rollback() {
  if [[ ! -f "$ROLLBACK_TARGET_FILE" ]]; then
    log "rollback unavailable: no previous successful release file"
    return 1
  fi

  # shellcheck disable=SC1090
  source "$ROLLBACK_TARGET_FILE"
  if [[ -z "${ANNOPILOT_IMAGE_TAG:-}" ]]; then
    log "rollback unavailable: previous release missing ANNOPILOT_IMAGE_TAG"
    return 1
  fi

  export ANNOPILOT_IMAGE_TAG
  log "rollback started image_tag=$ANNOPILOT_IMAGE_TAG"
  cd "$APP_DIR"
  docker_cmd compose -f "$COMPOSE_FILE" pull
  docker_cmd compose -f "$COMPOSE_FILE" up -d --remove-orphans
  if healthcheck; then
    write_release_file "$CURRENT_RELEASE_FILE" "rollback-ok"
    write_release_file "$LAST_SUCCESSFUL_FILE" "rollback-ok"
    log "rollback completed image_tag=$ANNOPILOT_IMAGE_TAG"
    return 0
  fi

  log "rollback healthcheck failed image_tag=$ANNOPILOT_IMAGE_TAG"
  return 1
}

mkdir -p "$APP_DIR/data/runtime" "$APP_DIR/data/projects" "$APP_DIR/backups" "$RELEASE_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another annopilot deployment is already running"
  exit 75
fi

log "deployment started mode=$DEPLOY_MODE sha=${ANNOPILOT_DEPLOY_SHA:-unknown} ref=${ANNOPILOT_DEPLOY_REF:-unknown} image_tag=$ANNOPILOT_IMAGE_TAG"
prepare_rollback_target

case "$DEPLOY_MODE" in
  image)
    require_file "$COMPOSE_FILE"
    cd "$APP_DIR"
    docker_cmd compose -f "$COMPOSE_FILE" pull
    docker_cmd compose -f "$COMPOSE_FILE" up -d --remove-orphans
    ;;
  source)
    REPO_DIR="${ANNOPILOT_REPO_DIR:-$APP_DIR/src/AnnoPilot}"
    TARGET_BRANCH="${ANNOPILOT_SOURCE_BRANCH:-main}"
    require_file "$COMPOSE_FILE"
    if [[ ! -d "$REPO_DIR/.git" ]]; then
      log "missing source checkout: $REPO_DIR"
      exit 1
    fi
    cd "$REPO_DIR"
    git fetch origin "$TARGET_BRANCH"
    git checkout "$TARGET_BRANCH"
    git pull --ff-only origin "$TARGET_BRANCH"
    cd "$APP_DIR"
    docker_cmd compose -f "$COMPOSE_FILE" build
    docker_cmd compose -f "$COMPOSE_FILE" up -d --remove-orphans
    ;;
  *)
    log "unsupported ANNOPILOT_DEPLOY_MODE: $DEPLOY_MODE"
    exit 2
    ;;
esac

docker_cmd image prune -f
if ! healthcheck; then
  log "deployment healthcheck failed; attempting rollback"
  rollback || true
  exit 1
fi

write_release_file "$CURRENT_RELEASE_FILE" "ok"
write_release_file "$LAST_SUCCESSFUL_FILE" "ok"
rm -f "$ROLLBACK_TARGET_FILE"

log "deployment completed"
