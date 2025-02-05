The yaesm test suite uses pytest.

All tests can assume that they are running as root in a Ubuntu Linux virtual machine.

For this reason tests don't have to worry about cleaning up state, which allows for easier debugging. It is very unwise to run the test suite outside of a virtual machine as your system may get mangled. Always use `vagrant-pytest` to run the test suite.

See `vagrant-pytest` and `Vagrantfile_pytest` for more information.
