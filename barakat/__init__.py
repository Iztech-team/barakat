# Keep in step with the `v<version>` git tag cut when this is promoted to `main`.
# Frappe surfaces this in the desk (Installed Applications) and in `bench version`,
# so it is how anyone on a live bench answers "which barakat is this site running?".
# 1.0.0 rather than 0.x: this runs in production for paying tenants.
__version__ = "1.0.0"
