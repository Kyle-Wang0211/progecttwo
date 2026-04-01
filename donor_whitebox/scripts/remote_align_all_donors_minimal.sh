#!/usr/bin/env bash
set -euo pipefail

bash /root/donor_whitebox/scripts/remote_align_hislam2_clean_minimal.sh
bash /root/donor_whitebox/scripts/remote_align_photoslam_clean_minimal.sh
bash /root/donor_whitebox/scripts/remote_align_monogs_minimal.sh
bash /root/donor_whitebox/scripts/remote_align_wildgs_clean_minimal.sh
