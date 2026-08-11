# Releasing basemode-loom

Releases are built and uploaded from a clean `main` checkout.

1. Update the version in `pyproject.toml` and run `uv lock`.
2. Move the relevant changelog entries from Unreleased into a dated release.
3. Run `make release-check`.
4. Commit the release preparation and push `main`; wait for CI to pass.
5. Tag the exact release commit locally:

   ```bash
   git tag -a v0.2.0 -m "basemode-loom 0.2.0"
   ```

6. Push the tag. The `Release` GitHub Actions workflow reruns the complete
   release checks, builds and validates both distributions, publishes them to
   PyPI with OIDC trusted publishing, and creates the GitHub release:

   ```bash
   git push origin v0.2.0
   ```

7. Confirm the new version on PyPI and smoke-test installation from the index.

The PyPI project must configure a trusted publisher for repository
`FergusFettes/basemode-loom` and workflow `release.yml`. The workflow does not
use a long-lived PyPI token. Run it manually with `workflow_dispatch` to test
the complete build path without publishing; only a pushed tag enables the
publish job.

The weekly Basemode patch workflow prepares and atomically pushes its release
commit and tag. The tag delegates publication to the same `Release` workflow.

PyPI does not permit replacing a published version. If artifact validation or
upload fails after a version has reached PyPI, prepare a new patch release.
