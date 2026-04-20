# SDN Learning Switch Controller

> **Course Assignment** — Software-Defined Networking  
> **Framework**: Ryu + Mininet + OpenFlow 1.3

---

## Problem Statement

Implement an SDN controller that mimics a **Layer-2 learning switch** by:

- Dynamically learning MAC addresses from incoming packets  
- Installing unicast OpenFlow flow rules once a destination is known  
- Flooding unknown destinations out all ports  
- Exposing the flow table for inspection and validation

Traditional switches learn MACs in hardware. Here the **Ryu controller** replicates that behaviour in software, demonstrating the full OpenFlow control-plane cycle.

---

## Architecture Overview

```
     H1 (10.0.0.1)          H3 (10.0.0.3)
          │                       │
     H2 (10.0.0.2)          H4 (10.0.0.4)
          │                       │
       ┌──┴──────────────────────┴──┐
       │   S1 ──────────── S2       │   ← OVS (OpenFlow 1.3)
       └────────────────────────────┘
                     │
             ┌───────┴───────┐
             │  Ryu Controller│  ← learning_switch.py
             │  127.0.0.1:6653│
             └───────────────┘
```

**Control plane flow (Packet-In → Flow-Mod):**

```
Host sends frame
      │
      ▼
OVS switch (table miss) ──► Packet-In ──► Ryu Controller
                                               │
                                    1. Learn src_mac → port
                                    2. Lookup dst_mac
                                         │
                                 ┌───────┴───────────┐
                              Known                Unknown
                                 │                   │
                          Flow-Mod install        Packet-Out
                          (unicast rule)           (flood)
                                 │
                          Packet-Out (fwd)
```

---

## Repository Structure

```
sdn-learning-switch/
├── learning_switch.py     
├── custom_topo.py         
├──  test_scenarios.py      
├── docs/
│   └── screenshots/           
└── README.md
```

---

## Prerequisites

| Software | Version | Install |
|----------|---------|---------|
| Ubuntu   | 20.04 / 22.04 | — |
| Python   | ≥ 3.8 | `sudo apt install python3` |
| Mininet  | ≥ 2.3 | See below |
| Open vSwitch | ≥ 2.13 | `sudo apt install openvswitch-switch` |
| Ryu SDN Framework | ≥ 4.34 | `pip install ryu` |
| Wireshark (optional) | any | `sudo apt install wireshark` |

### Install Mininet from source (recommended)

```bash
git clone https://github.com/mininet/mininet
cd mininet
sudo util/install.sh -a        # full install including OVS
```

### Install Ryu

```bash
pip install ryu
# If dependency issues arise:
pip install eventlet==0.30.2 ryu
```

---

## Setup & Execution

### Step 1 — Clone the repository

```bash
git clone https://github.com/<your-username>/sdn-learning-switch.git
```

### Step 2 — Start the Ryu controller

Open **Terminal 1**:


```bash
cd ~/sdn-learning-switch
source ryu-env/bin/activate
ryu-manager learning_switch.py --verbose
```
You should see:

```
============================================================
  SDN Learning Switch Controller — STARTED
============================================================
```

### Step 3 — Launch the Mininet topology

Open **Terminal 2**:

```bash
sudo python3 custom_topo.py
```

You should see the Mininet CLI prompt:

```
*** Topology ready — hints:
  mininet> pingall

  mininet> h1 ping -c3 h3

  mininet> s1 ovs-ofctl dump-flows s1

  mininet> s1 ip link set s1-eth3 down
  mininet> h1 ping -c3 h3

  mininet> s1 ip link set s1-eth3 down
  mininet> h1 ping -c3 h3

mininet>
```

### Step 4 — Run the test scenarios

Open **Terminal 3**:

```bash
# Unit tests only (no network required)
python3 test_scenarios.py
```

---

## Controller Logic Deep-Dive

### MAC Learning (`packet_in_handler`)

```python
# 1. Extract source MAC and incoming port
src_mac = eth.src
in_port  = msg.match["in_port"]

# 2. Store/update the MAC → port mapping
self.mac_to_port[dpid][src_mac] = in_port

# 3. Lookup destination
if dst_mac in self.mac_to_port[dpid]:
    out_port = self.mac_to_port[dpid][dst_mac]   # unicast
else:
    out_port = OFPP_FLOOD                          # unknown → flood
```

### Flow Rule Installation

```python
match = parser.OFPMatch(
    in_port=in_port,
    eth_dst=dst_mac,
    eth_src=src_mac,
)
actions = [parser.OFPActionOutput(out_port)]

# Install with 30-second idle timeout
self._install_flow(datapath,
                   priority=10,
                   match=match,
                   actions=actions,
                   idle_timeout=30)
```

### Table-Miss Entry

Installed once per switch on connection:

```
priority=0, match=*, actions=OUTPUT:CONTROLLER
```

This catch-all ensures every unknown packet reaches the controller.

---

## Test Scenarios

### Scenario A — Normal Forwarding vs Flooding

| Step | Action | Expected Behaviour |
|------|--------|-------------------|
| 1 | `h1 ping h2` (first time) | Controller **floods** (H2 MAC unknown) |
| 2 | H2 replies to H1 | Controller **learns** H2→port2 |
| 3 | `h1 ping h2` (second time) | Controller **forwards** via installed rule |
| 4 | `s1 ovs-ofctl dump-flows s1` | Unicast rules visible with `idle_timeout=30` |

```bash
# Run from Mininet CLI
mininet> h1 ping -c5 h2
mininet> s1 ovs-ofctl dump-flows s1 -O OpenFlow13
```

### Scenario B — Normal vs Failure

| Step | Action | Expected Behaviour |
|------|--------|-------------------|
| 1 | `pingall` | All 4 hosts reachable |
| 2 | Bring down S1–S2 link | `ip link set s1-eth3 down` |
| 3 | `h1 ping h3` | **Unreachable** (cross-switch, link broken) |
| 4 | `h1 ping h2` | **Still reachable** (same switch) |
| 5 | Restore link | `ip link set s1-eth3 up` |
| 6 | `pingall` | All 4 hosts reachable again |

```bash
# From Mininet CLI:
mininet> pingall
mininet> s1 ip link set s1-eth3 down
mininet> h1 ping -c3 h3    # should fail
mininet> h1 ping -c3 h2    # should succeed
mininet> s1 ip link set s1-eth3 up
mininet> pingall            # all green again
```

---

## Flow Rule Inspection

### Dump all flows on S1

```bash
sudo ovs-ofctl dump-flows s1 -O OpenFlow13
```

### Expected output (after h1↔h2 ping)

```
OFPST_FLOW reply (OF1.3):
 cookie=0x0, duration=8.3s, table=0, n_packets=5, n_bytes=490,
   idle_timeout=30, priority=10,
   in_port=1,dl_src=aa:bb:00:00:00:01,dl_dst=aa:bb:00:00:00:02
   actions=output:2

 cookie=0x0, duration=8.1s, table=0, n_packets=4, n_bytes=392,
   idle_timeout=30, priority=10,
   in_port=2,dl_src=aa:bb:00:00:00:02,dl_dst=aa:bb:00:00:00:01
   actions=output:1

 cookie=0x0, duration=60.0s, table=0, n_packets=12, n_bytes=1092,
   priority=0 actions=CONTROLLER:65535
```

### Wireshark capture filter (for OpenFlow traffic)

```
openflow_v4 or (tcp.port == 6653)
```

---

## Expected Output

### Controller terminal (Ryu)

```
14:02:01 [INFO] Switch connected  dpid=0000000000000001
14:02:01 [INFO] Table-miss entry installed on dpid=0000000000000001
14:02:05 [INFO] [LEARN]   dpid=..0001  mac=aa:bb:00:00:00:01 → port=1
14:02:05 [INFO] [FLOOD]   dpid=..0001  aa:bb:00:00:00:01 → aa:bb:00:00:00:02  (unknown dst)
14:02:05 [INFO] [LEARN]   dpid=..0001  mac=aa:bb:00:00:00:02 → port=2
14:02:05 [INFO] [FORWARD] dpid=..0001  aa:bb:00:00:00:02 → aa:bb:00:00:00:01  via port=1
14:02:15 [INFO] [FORWARD] dpid=..0001  aa:bb:00:00:00:01 → aa:bb:00:00:00:02  via port=2
```

### Unit test output

```
test_broadcast_dst_always_floods ... ok
test_known_dst_forwards ... ok
test_mac_table_is_per_switch ... ok
test_multiple_hosts_independent ... ok
test_port_migration_updates_table ... ok
test_unknown_dst_floods ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.001s
OK
```
---

## Proof of Execution


| File                      | Description        |
| ------------------------- | ------------------ |
| `01_controller_start.png` | Controller startup |
| `02_pingall.png`          | Ping results       |
| `03_learning_logs.png`    | Learning behavior  |
| `04_flow_table.png`       | Flow rules         |
| `05_link_failure.png`     | Failure scenario   |
| `06_recovery.png`         | Network restored   |
| `07_test.png`             | Test results       |
| `08_wireshark.png`        | OpenFlow capture   |


---

