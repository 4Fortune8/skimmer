#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
service_name="skimmer-workflow.service"
source_unit="${repository_root}/deploy/systemd/${service_name}"
installed_unit="/etc/systemd/system/${service_name}"

sudo install -m 0644 "${source_unit}" "${installed_unit}"
sudo systemctl daemon-reload
sudo systemctl enable "${service_name}"
sudo systemctl restart "${service_name}"
sudo systemctl --no-pager --full status "${service_name}"
