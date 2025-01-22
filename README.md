# YAESM

A backup tool for multiple file systems!

# DEVELOPERS

Please do not hesitate to open an [issue](https://github.com/Vultimate1/yaesm/issues/new) or [pull request](https://github.com/Vultimate1/yaesm/pulls)!

To run the test suite execute the `docker-pytest.sh` script from the root of the yaesm source tree:

```
$ ./docker-pytest.sh -H ### print docker-pytest.sh help message
$ ./docker-pytest.sh ./tests
$ ./docker-pytest.sh $PYTEST_ARGS
```

Note that all tests assume they are running in a containerized environment. It is NOT SAFE to run the test suite without the use of `docker-pytest.sh`. See `tests/README` for more information about the test suite.
