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

6. Put a PyPI API token in `.env` as `UV_PUBLISH_TOKEN`, then publish from the
   clean tagged checkout:

   ```bash
   make publish
   ```

7. Confirm the new version on PyPI, push the tag with `git push origin v0.2.0`,
   and create GitHub release notes from the matching changelog section.

PyPI does not permit replacing a published version. If artifact validation or
upload fails after a version has reached PyPI, prepare a new patch release.
