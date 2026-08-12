#!/usr/bin/env bash
set -euo pipefail

bucket="${1:-<PROJECT_SLUG>-repo-backups-<AWS_ACCOUNT_ID>}"
object="${2:-latest/<PROJECT_SLUG>.git.tar.gz}"
expected_sha="${3:-}"
repo_name="<PROJECT_SLUG>"
workdir="$(mktemp -d "${TMPDIR:-/tmp}/XYZ-PILOT-088-restore.XXXXXX")"

cleanup() {
  rm -rf "${workdir}"
}
trap cleanup EXIT

archive="${workdir}/restore.git.tar.gz"
checksum="${archive}.sha256"

aws s3 cp "s3://${bucket}/${object}" "${archive}" --only-show-errors
if aws s3 cp "s3://${bucket}/${object}.sha256" "${checksum}" --only-show-errors; then
  expected_checksum="$(awk '{print $1; exit}' "${checksum}")"
  if command -v sha256sum >/dev/null 2>&1; then
    actual_checksum="$(sha256sum "${archive}" | awk '{print $1}')"
  else
    actual_checksum="$(shasum -a 256 "${archive}" | awk '{print $1}')"
  fi
  if [[ "${actual_checksum}" != "${expected_checksum}" ]]; then
    echo "Checksum mismatch for s3://${bucket}/${object}" >&2
    echo "Expected: ${expected_checksum}" >&2
    echo "Actual:   ${actual_checksum}" >&2
    exit 1
  fi
else
  echo "No checksum object found for s3://${bucket}/${object}; continuing with git fsck validation only." >&2
fi

tar -C "${workdir}" -xzf "${archive}"
git -C "${workdir}/${repo_name}.git" fsck --full

restored_sha="$(git -C "${workdir}/${repo_name}.git" rev-parse refs/heads/main)"
if [[ -n "${expected_sha}" && "${restored_sha}" != "${expected_sha}" ]]; then
  echo "Restored refs/heads/main ${restored_sha} does not match expected ${expected_sha}" >&2
  exit 1
fi

echo "Restore validation passed for s3://${bucket}/${object}"
echo "Restored refs/heads/main: ${restored_sha}"
