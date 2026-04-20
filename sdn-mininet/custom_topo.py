#!/usr/bin/env python3
"""
Mininet Topology for SDN Learning Switch
=========================================
Creates a two-switch, four-host topology:

        H1 ──┐           ┌── H3
             S1 ──────── S2
        H2 ──┘           └── H4

  H1 : 10.0.0.1 / aa:bb:00:00:00:01
  H2 : 10.0.0.2 / aa:bb:00:00:00:02
  H3 : 10.0.0.3 / aa:bb:00:00:00:03
  H4 : 10.0.0.4 / aa:bb:00:00:00:04

Both switches connect to the Ryu controller on 127.0.0.1:6653.

Usage
-----
  sudo python3 topology/custom_topo.py

Interactive Mininet CLI is opened after setup so you can run:
  mininet> pingall
  mininet> h1 ping h3
  mininet> h1 iperf h2
  mininet> s1 ovs-ofctl dump-flows s1
"""

from mininet.net     import Mininet
from mininet.node    import RemoteController, OVSKernelSwitch
from mininet.cli     import CLI
from mininet.log     import setLogLevel, info
from mininet.link    import TCLink


def build_topology():
    """Build and return the Mininet network object."""
    net = Mininet(
        controller=RemoteController,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=False,
    )

    info("*** Adding Ryu controller\n")
    c0 = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653,
    )

    info("*** Adding switches\n")
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", protocols="OpenFlow13")

    info("*** Adding hosts\n")
    h1 = net.addHost("h1", ip="10.0.0.1/24", mac="aa:bb:00:00:00:01")
    h2 = net.addHost("h2", ip="10.0.0.2/24", mac="aa:bb:00:00:00:02")
    h3 = net.addHost("h3", ip="10.0.0.3/24", mac="aa:bb:00:00:00:03")
    h4 = net.addHost("h4", ip="10.0.0.4/24", mac="aa:bb:00:00:00:04")

    info("*** Adding links (100 Mbps, 5 ms delay)\n")
    bw, delay = 100, "5ms"
    net.addLink(h1, s1, bw=bw, delay=delay)
    net.addLink(h2, s1, bw=bw, delay=delay)
    net.addLink(s1, s2, bw=bw, delay=delay)
    net.addLink(h3, s2, bw=bw, delay=delay)
    net.addLink(h4, s2, bw=bw, delay=delay)

    return net, c0


def run():
    setLogLevel("info")

    net, c0 = build_topology()

    info("*** Starting network\n")
    net.start()

    # Force OpenFlow 1.3 on both switches and point them at the controller
    for sw in [net.get("s1"), net.get("s2")]:
        sw.cmd(f"ovs-vsctl set bridge {sw.name} protocols=OpenFlow13")
        sw.cmd(
            f"ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6653"
        )

    info("\n*** Topology ready — hints:\n")
    info("  mininet> pingall\n")
    info("  mininet> h1 ping -c3 h3\n")
    info("  mininet> h1 iperf h2 &\n")
    info("  mininet> s1 ovs-ofctl dump-flows s1\n")
    info("  mininet> s2 ovs-ofctl dump-flows s2\n\n")

    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    run()
