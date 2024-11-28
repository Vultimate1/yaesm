#!/bin/sh

SCRIPT=$(basename "$0")
IMAGE_NAME=yaesm-pytest
DOCKERFILE=Dockerfile_pytest

HELP_MSG=$(cat <<END_HELP_MSG
Usage: ./$SCRIPT [-B] [-D] [-H] [-K] [-P] <pytest-args..>

$SCRIPT runs the yaesm test suite with pytest inside a Docker container built
using the '$DOCKERFILE' Dockerfile. The image for this container is named
'$IMAGE_NAME'. If an image with the name '$IMAGE_NAME' does not exist, it will
be built automatically using '$DOCKERFILE'. This program works as a wrapper
around pytest, running it within the Docker container. See '$DOCKERFILE' for
more information about the container environment.

Options:
  -B      Force rebuild of the '$IMAGE_NAME' Docker image.
  -D      Run this program in debug mode (set -x).
  -H      Print this help message and exit 0.
  -K      Don't use the --rm docker run option.
  -P      Don't use the --privileged=true docker run option.

Examples:
  ./$SCRIPT tests
  ./$SCRIPT -H
  ./$SCRIPT -B -D -K -P --show-capture=stdout --fixtures-per-test tests/yaesm/*
END_HELP_MSG
)

BUILD_IMAGE=1
DOCKER_RUN_OPTS='-v .:/mnt-yaesm/yaesm --rm --privileged=true'
PYTEST_OPTS=''

while [ -n "$1" ]; do
    case "$1" in
        -B)
          BUILD_IMAGE=0
          shift
          ;;
        -D)
          printf '%s: turning on debug mode (set -x)\n' "$SCRIPT" 2>&1
          PS4="$SCRIPT DEBUG: "
          set -x
          shift
          ;;
        -H)
          printf '%s\n' "$HELP_MSG"
          exit 0
          ;;
        -K)
          DOCKER_RUN_OPTS=$(printf '%s' "$DOCKER_RUN_OPTS" | sed 's/--rm//')
          shift
          ;;
        -P)
          DOCKER_RUN_OPTS=$(printf '%s' "$DOCKER_RUN_OPTS" | sed 's/--privileged=true//')
          shift
          ;;
        *)
          PYTEST_OPTS="$PYTEST_OPTS $1"
          shift
          ;;
    esac
done

DOCKER=$(command -v docker 2>/dev/null)
if [ -z "$DOCKER" ]; then
    printf '%s: could not find docker command. Exiting.\n' "$SCRIPT" 1>&2
    exit 1
fi

$DOCKER inspect $IMAGE_NAME >/dev/null 2>&1
IMAGE_EXISTS=$?

YAESM_DIR=$(dirname "$0")
cd "$YAESM_DIR" || exit 1

if [ $IMAGE_EXISTS -ne 0 ] || [ $BUILD_IMAGE -eq 0 ]; then
    $DOCKER build --no-cache -t $IMAGE_NAME -f $DOCKERFILE .
    BUILD_SUCCEEDED=$?
    if ! $BUILD_SUCCEEDED; then
        printf '%s: docker build command exited with status %d. Exiting.\n' "$SCRIPT" "$BUILD_SUCCEEDED" 1>&2
        exit 1
    fi
fi

exec $DOCKER run $DOCKER_RUN_OPTS $IMAGE_NAME $PYTEST_OPTS
