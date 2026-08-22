# YAESM

A backup tool with support for multiple file systems!

See the [manual](man/yaesm.1.md) for usage and configuration details.

# DEVELOPERS

Please do not hesitate to open an [issue](https://github.com/Vultimate1/yaesm/issues/new) or [pull request](https://github.com/Vultimate1/yaesm/pulls)!

To run the test suite execute the `vagrant-pytest` script from the root of the yaesm source tree:

```
$ ./vagrant-pytest -H ### print help message
$ ./vagrant-pytest ./tests
$ ./vagrant-pytest $PYTEST_ARGS
```

All tests assume they are running in a virtual machine environment. It is NOT SAFE to run the test suite without the use of `vagrant-pytest`! For more information see the files `vagrant-pytest`, `Vagrantfile_pytest`, and `tests/README.md`.

Note that to run the test suite you will need to install [Vagrant](https://www.vagrantup.com/) and [libvirt](https://libvirt.org/) on your system.

# RELEASE

Yaesm uses `X.Y.Z` release versions. Debian and Fedora packages are generated
with package revision `1`; a packaging correction requires a new Yaesm patch
release rather than a separate package revision.

Follow these steps to create a new release:

1. **Update version in `pyproject.toml`**
   - Update the `version` field to the new version (e.g., `0.0.2`)
   - This is the only version that must be updated manually; CI generates the
     package versions and the manual's version footer from it

2. **Update CHANGELOG.md**
   - Find the `## [Unreleased]` section
   - Replace it with `## [X.X.X] - YYYY-MM-DD` (use the version from step 1 and today's date)
   - Add a new `## [Unreleased]` section at the top of the changelog

3. **Commit and merge your changes**
   - Have the commit message follow the template: `Release vX.X.X`

4. **Wait for CI to finish**
   - Confirm that the tests and package builds pass
   - The manual workflow will commit the regenerated man page to `main`

5. **Create the release on GitHub**
   - Go to **Releases** → **Create a new release**
   - **Tag name:** `vX.X.X` (must match the version from `pyproject.toml`)
   - **Target branch:** `main`
   - **Title:** `Release vX.X.X`
   - Add release notes (or use "Generate release notes")
   - Click **Publish release**
