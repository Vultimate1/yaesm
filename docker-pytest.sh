#!/bin/sh

HELP_MSG=$(cat <<END_HELP_MSG
Usage: ./docker-pytest.sh [-B] [-D] [-H] [-K] [-P] [-R <opts>] <pytest-args..>

docker-pytest.sh is a wrapper aroung pytest, running it inside a docker
container built using the 'Dockerfile_pytest' Dockerfile. This program expects
to be run from the root of the yaesm source tree. The image for the docker
container is named 'yaesm-pytest'. If an image with the name 'yaesm-pytest' does
not exist, one will be built automatically using 'Dockerfile_pytest'. By default
the options '-v .:/mnt-yaesm/yaesm --rm --privileged=true' are used for
'docker run'. See the file 'Dockerfile_pytest' for more information about the
container environment.

Options:
  -B          Force rebuild of the 'yaesm-pytest' Docker image
  -D          Run this program in debug mode (set -x)
  -H          Print this help message and exit 0
  -K          Don't use the --rm 'docker run' option
  -P          Don't use the --privileged=true 'docker run' option
  -R <opts>   Pass 'opts' as extra 'docker run' options

Examples:
  ./docker-pytest.sh tests
  ./docker-pytest.sh -H
  ./docker-pytest.sh -B -D -K -P --show-capture=stdout --fixtures-per-test ./tests
  ./docker-pytest.sh -R '--detach --stop-timeout 300' ./tests
END_HELP_MSG
)

BUILD_IMAGE=1
DOCKER_RUN_OPTS='-v .:/mnt-yaesm/yaesm --rm --privileged=true'
PYTEST_OPTS=''

# This script uses single capital letter opts to not clash with pytest opts.
while [ -n "$1" ]; do
    case "$1" in
        -B)
          BUILD_IMAGE=0
          shift
          ;;
        -D)
          printf './docker-pytest.sh: turning on debug mode (set -x)\n' 2>&1
          PS4="./docker-pytest.sh DEBUG: "
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
        -R)
          DOCKER_RUN_OPTS="$DOCKER_RUN_OPTS $2"
          shift 2
          ;;
        *)
          PYTEST_OPTS="$PYTEST_OPTS $1"
          shift
          ;;
    esac
done

DOCKER=$(command -v docker 2>/dev/null)
if [ -z "$DOCKER" ]; then
    printf './docker-pytest.sh: could not find docker command. Exiting.\n' 1>&2
    exit 1
fi

$DOCKER inspect yaesm-pytest >/dev/null 2>&1
IMAGE_EXISTS=$?

YAESM_DIR=$(dirname "$0")
cd "$YAESM_DIR" || exit 1

if [ $IMAGE_EXISTS -ne 0 ] || [ $BUILD_IMAGE -eq 0 ]; then
    $DOCKER build --no-cache -t yaesm-pytest -f Dockerfile_pytest .
    BUILD_SUCCEEDED=$?
    if [ $BUILD_SUCCEEDED -ne 0 ]; then
        printf './docker-pytest.sh: docker build command exited with status %d. Exiting.\n' "$BUILD_SUCCEEDED" 1>&2
        exit 1
    fi
fi

exec $DOCKER run $DOCKER_RUN_OPTS yaesm-pytest $PYTEST_OPTS
