#!/bin/sh

CWD=$(pwd)

HELP_MSG=$(cat <<END_HELP_MSG
TODO
END_HELP_MSG
)

die() {
    if [ -n "$1" ]; then
        format=$1
        shift
        1>&2 printf "./vagrant-pytest.sh: error: $format\n" $@
    fi
    exit 1
}

assert_env() {
    which vagrant    >/dev/null 2>&1 || die 'vagrant not in $PATH'
    which virtualbox >/dev/null 2>&1 || die 'virtualbox not in $PATH'
    which rsync      >/dev/null 2>&1 || die 'rsync not in $PATH'
    if [ $(basename $CWD) != "yaesm" -o ! -f Vagrantfile -o ! -d src -o ! -d tests ]; then
        die "not in yaesm root directory"
    fi
}

PYTEST_OPTS=''
VM_REBUILD=1
VM_KEEP_RUNNING=1
VM_SAVE_MACHINE_STATE=1
parse_opts() {
    # This script uses single capital letter opts to not clash with pytest opts.
    while [ -n "$1" ]; do
        case "$1" in
            -B)
              VM_REBUILD=0
              shift
              ;;
            -D)
              PS4="./vagrant-pytest.sh: DEBUG: "
              set -x
              shift
              ;;
            -H)
              printf '%s\n' "$HELP_MSG"
              exit 0
              ;;
            -K)
              VM_KEEP_RUNNING=0
              shift
              ;;
            -S)
              VM_SAVE_MACHINE_STATE=0
              shift
              ;;
            *)
              PYTEST_OPTS="$PYTEST_OPTS $1"
              shift
              ;;
        esac
    done
}

default_snapshot() {
    # if default snapshot doesn't exist then create it
    snapshot_list=$(vagrant snapshot list --machine-readable yaesm-pytest)
    vagrant snapshot list yaesm-pytest --machine-readable | grep yaesm-pytest-snapshot-pure
    snapshot_pure_exists=$?
}

STATUS=$(vagrant status yaesm-pytest --machine-readable | grep ',state,')
VM_BUILT=$?

if [ $VM_REBUILD -eq 0 ] && [ $VM_BUILT -eq 0 ]; then
    echo 'HERE: vagrant destroy -f yaesm-pytest'
    # vagrant destroy -f yaesm-pytest
fi

printf '%s\n' "$STATUS" | grep ',state,running$' >/dev/null
VM_RUNNING=$?

if [ $VM_RUNNING -ne 0 ]; then
    vagrant up yaesm-pytest --provider virtualbox
fi

vagrant snapshot save yaesm-pytest-
vagrant ssh yaesm-pytest -c "cd /yaesm && pytest $PYTEST_OPTS"
vagrant snapshot pop yaesm-pytest

if [ $VM_KEEP_RUNNING -ne 0 ]; then
    vagrant halt yaesm-pytest
fi
