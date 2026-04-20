#!/usr/bin/env bash
# =============================================================================
# run_tests.sh — Execute both SDN test scenarios and capture logs
# =============================================================================
# Usage:  sudo bash tests/run_tests.sh
# =============================================================================
set -euo pipefail

CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
TS=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/test_run_$TS.log"

header() { echo -e "\n${CYAN}══════════════════════════════════════════════${RESET}"; \
           echo -e "${CYAN}  $1${RESET}"; \
           echo -e "${CYAN}══════════════════════════════════════════════${RESET}"; }

ok()   { echo -e "${GREEN}  ✔  $1${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠  $1${RESET}"; }
err()  { echo -e "${RED}  ✘  $1${RESET}"; }

# ── Preflight checks ─────────────────────────────────────────────────────────
header "Preflight Checks"

if [[ $EUID -ne 0 ]]; then
    err "Must run as root (sudo bash tests/run_tests.sh)"
    exit 1
fi
ok "Running as root"

command -v python3    >/dev/null 2>&1 && ok "python3 found"    || { err "python3 missing"; exit 1; }
command -v ovs-vsctl  >/dev/null 2>&1 && ok "OVS found"        || warn "OVS not found — integration tests will be skipped"
command -v ryu-manager>/dev/null 2>&1 && ok "ryu-manager found" || warn "ryu-manager not found — integration tests will be skipped"

# ── Unit Tests ────────────────────────────────────────────────────────────────
header "Scenario A — MAC Learning Logic (Unit Tests)"
echo "Running controller unit tests..." | tee -a "$LOG_FILE"
python3 -m pytest tests/test_scenarios.py::TestMACLearningLogic \
    -v --tb=short 2>&1 | tee -a "$LOG_FILE" || true

header "Scenario B — Flow Table Regression Tests"
python3 -m pytest tests/test_scenarios.py::TestFlowTableRegression \
    -v --tb=short 2>&1 | tee -a "$LOG_FILE" || true

# ── Integration Tests (if controller is running) ──────────────────────────────
if nc -z -w1 127.0.0.1 6653 2>/dev/null; then
    header "Integration Tests (controller detected on :6653)"
    python3 -m pytest tests/test_scenarios.py::TestNetworkScenarios \
        -v --tb=short 2>&1 | tee -a "$LOG_FILE" || true
else
    warn "Controller not running — skipping integration tests"
    echo "  Start with:  ryu-manager controller/learning_switch.py"
fi

header "Done — log saved to $LOG_FILE"
