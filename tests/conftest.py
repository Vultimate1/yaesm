import pytest
import subprocess

@pytest.fixture
def superuser():
    """Fixture to skip the test if we are not the superuser."""
    whoami = subprocess.run(["whoami"], capture_output=True, encoding="utf-8")
    if "root" != whoami.stdout.removesuffix("\n"):
        pytest.skip("superuser required")
    return True

@pytest.fixture
def loopback_generator(superuser, tmp_path_factory):
    """Fixture to generate and cleanup loopback devices."""
    generated_loopbacks = []

    def generator():
        loopfile = tmp_path_factory.mktemp("loop") / "loop"
        subprocess.run(["truncate", "--size", "1G", loopfile], check=True)
        losetup = subprocess.run(["losetup", "--find", "--show", loopfile], check=True, capture_output=True, encoding="utf-8")
        loop = losetup.stdout.removesuffix("\n")
        generated_loopbacks.append(loop)
        return loop

    yield generator

    for loop in generated_loopbacks:
        subprocess.run(["losetup", "--detach", loop], check=True)

@pytest.fixture
def loopback(loopback_generator):
    """Useful fixture for when a test only needs a single loopback device."""
    return loopback_generator()
