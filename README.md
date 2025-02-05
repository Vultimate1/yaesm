# YAESM

A backup tool for multiple file systems!

# DEVELOPERS

Please do not hesitate to open an [issue](https://github.com/Vultimate1/yaesm/issues/new) or [pull request](https://github.com/Vultimate1/yaesm/pulls)!

To run the test suite execute the `vagrant-pytest` script from the root of the yaesm source tree:

```
$ ./vagrant-pytest -H ### print help message
$ ./vagrant-pytest ./tests
$ ./vagrant-pytest $PYTEST_ARGS
```

All tests assume they are running in a virtual machine environment. It is NOT SAFE to run the test suite without the use of `vagrant-pytest`! For more information see the files `vagrant-pytest`, `Vagrantfile_pytest`, and `tests/README`.
