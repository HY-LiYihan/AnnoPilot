#!/usr/bin/env bash
set -Eeuo pipefail

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

APP_DIR="${ROSETTA_APP_DIR:-/opt/rosetta}"
COMPOSE_FILE="${ROSETTA_COMPOSE_FILE:-$APP_DIR/compose.yml}"
DEPLOY_MODE="${ROSETTA_DEPLOY_MODE:-image}"
LOCK_FILE="${ROSETTA_DEPLOY_LOCK:-/tmp/rosetta-deploy.lock}"
HEALTH_URL="${ROSETTA_HEALTH_URL:-http://127.0.0.1:8501/api/health}"
HEALTH_RETRIES="${ROSETTA_HEALTH_RETRIES:-30}"
HEALTH_SLEEP_SECONDS="${ROSETTA_HEALTH_SLEEP_SECONDS:-2}"
DOCKER_COMMAND="${ROSETTA_DOCKER_COMMAND:-docker}"

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

mkdir -p "$APP_DIR/data/runtime" "$APP_DIR/data/projects" "$APP_DIR/backups"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another rosetta deployment is already running"
  exit 75
fi

log "deployment started mode=$DEPLOY_MODE sha=${ROSETTA_DEPLOY_SHA:-unknown} ref=${ROSETTA_DEPLOY_REF:-unknown}"

case "$DEPLOY_MODE" in
  image)
    require_file "$COMPOSE_FILE"
    cd "$APP_DIR"
    docker_cmd compose -f "$COMPOSE_FILE" pull
    docker_cmd compose -f "$COMPOSE_FILE" up -d --remove-orphans
    ;;
  source)
    REPO_DIR="${ROSETTA_REPO_DIR:-$APP_DIR/src/AnnoPilot}"
    TARGET_BRANCH="${ROSETTA_SOURCE_BRANCH:-main}"
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
    log "unsupported ROSETTA_DEPLOY_MODE: $DEPLOY_MODE"
    exit 2
    ;;
esac

docker_cmd image prune -f
healthcheck

log "deployment completed"
