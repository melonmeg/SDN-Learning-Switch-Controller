"""
SDN Learning Switch Controller — Ryu Framework
================================================
Implements a Layer-2 learning switch using OpenFlow 1.3.

Behaviour:
  1. On every Packet-In the controller learns (src_mac → in_port) per switch.
  2. If the destination MAC is known a unicast flow rule is installed and the
     packet is forwarded.  If unknown the packet is flooded out all ports.
  3. Flow rules are installed with idle_timeout=30 s so stale entries expire.
  4. A Stats-Request loop lets operators inspect the live flow table.

Author : SDN Assignment
Date   : 2025
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4, icmp, tcp, udp
from ryu.lib import hub
import logging
import time


# --------------------------------------------------------------------------- #
#  Logging                                                                     #
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


class LearningSwitchController(app_manager.RyuApp):
    """
    Ryu application that mimics a traditional learning switch.

    Data structures
    ---------------
    mac_to_port : dict[dpid, dict[mac, port]]
        Per-switch MAC address table (learned dynamically from Packet-In events).
    flow_stats  : dict[dpid, list]
        Cached flow-table stats fetched periodically for diagnostics.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # Tunable constants
    FLOW_IDLE_TIMEOUT  = 30   # seconds before an inactive flow is evicted
    FLOW_HARD_TIMEOUT  = 0    # 0 = no hard timeout
    FLOW_PRIORITY      = 10   # higher than the default table-miss entry (0)
    TABLE_MISS_PRIORITY = 0
    STATS_POLL_INTERVAL = 10  # seconds between flow-table polling

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # mac_to_port[dpid][mac] = port_number
        self.mac_to_port: dict = {}

        # flow_stats[dpid] = list of OFPFlowStats objects
        self.flow_stats: dict = {}

        # Counters for observability
        self._pkt_count   = 0
        self._flood_count = 0
        self._fwd_count   = 0
        self._learn_count = 0

        # Background thread for periodic stats polling
        self._stats_thread = hub.spawn(self._stats_poll_loop)

        self.logger.info("=" * 60)
        self.logger.info("  SDN Learning Switch Controller — STARTED")
        self.logger.info("=" * 60)

    # ----------------------------------------------------------------------- #
    #  OpenFlow Handshake                                                      #
    # ----------------------------------------------------------------------- #

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        Called once per switch on initial connection.
        Installs a table-miss flow entry that sends all unmatched packets to
        the controller via Packet-In.
        """
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        dpid     = datapath.id

        self.logger.info("Switch connected  dpid=%016x", dpid)

        # Initialise per-switch MAC table
        self.mac_to_port.setdefault(dpid, {})

        # Table-miss: match everything, priority 0, send to controller
        match  = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._install_flow(datapath,
                           priority=self.TABLE_MISS_PRIORITY,
                           match=match,
                           actions=actions,
                           idle_timeout=0,
                           hard_timeout=0)
        self.logger.info("Table-miss entry installed on dpid=%016x", dpid)

    # ----------------------------------------------------------------------- #
    #  Core Packet-In Handler                                                  #
    # ----------------------------------------------------------------------- #

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """
        Main learning-switch logic executed for every Packet-In event.

        Steps
        -----
        1. Parse packet headers.
        2. Learn src_mac → in_port.
        3. Lookup dst_mac.
        4a. Known dst  → install unicast flow rule + forward.
        4b. Unknown dst → flood.
        """
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        dpid     = datapath.id
        in_port  = msg.match["in_port"]

        # Parse packet
        pkt  = packet.Packet(msg.data)
        eth  = pkt.get_protocols(ethernet.ethernet)[0]

        # Ignore LLDP (link-layer discovery) frames
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src_mac = eth.src
        dst_mac = eth.dst
        self._pkt_count += 1

        # ------------------------------------------------------------------ #
        #  Step 2 — MAC Learning                                              #
        # ------------------------------------------------------------------ #
        prev_port = self.mac_to_port[dpid].get(src_mac)
        self.mac_to_port[dpid][src_mac] = in_port

        if prev_port is None:
            self._learn_count += 1
            self.logger.info(
                "[LEARN] dpid=%016x  mac=%s → port=%s",
                dpid, src_mac, in_port,
            )
        elif prev_port != in_port:
            # Port migration — host moved
            self.logger.warning(
                "[MIGRATE] dpid=%016x  mac=%s  old_port=%s  new_port=%s",
                dpid, src_mac, prev_port, in_port,
            )

        # ------------------------------------------------------------------ #
        #  Step 3 — Lookup destination                                        #
        # ------------------------------------------------------------------ #
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # ------------------------------------------------------------------ #
        #  Step 4 — Install flow rule (unicast only)                          #
        # ------------------------------------------------------------------ #
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port,
                                    eth_dst=dst_mac,
                                    eth_src=src_mac)
            # Only install if the buffer is valid
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self._install_flow(datapath,
                                   priority=self.FLOW_PRIORITY,
                                   match=match,
                                   actions=actions,
                                   buffer_id=msg.buffer_id,
                                   idle_timeout=self.FLOW_IDLE_TIMEOUT,
                                   hard_timeout=self.FLOW_HARD_TIMEOUT)
            else:
                self._install_flow(datapath,
                                   priority=self.FLOW_PRIORITY,
                                   match=match,
                                   actions=actions,
                                   idle_timeout=self.FLOW_IDLE_TIMEOUT,
                                   hard_timeout=self.FLOW_HARD_TIMEOUT)
                self._send_packet(datapath, msg.buffer_id, in_port,
                                  actions, msg.data)
            self._fwd_count += 1
            self.logger.info(
                "[FORWARD] dpid=%016x  %s → %s  via port=%s",
                dpid, src_mac, dst_mac, out_port,
            )
        else:
            # Flood — send out all ports except the in_port
            self._send_packet(datapath, msg.buffer_id, in_port,
                              actions, msg.data)
            self._flood_count += 1
            self.logger.info(
                "[FLOOD]   dpid=%016x  %s → %s  (unknown dst)",
                dpid, src_mac, dst_mac,
            )

    # ----------------------------------------------------------------------- #
    #  Flow Statistics                                                         #
    # ----------------------------------------------------------------------- #

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        """Receives and stores the flow-table stats for each switch."""
        dpid = ev.msg.datapath.id
        flows = ev.msg.body
        self.flow_stats[dpid] = flows

        self.logger.info(
            "--- Flow Table (dpid=%016x) --------------------------------", dpid
        )
        self.logger.info(
            "  %-4s  %-6s  %-22s  %-22s  %-8s  %-10s  %s",
            "Prio", "Port", "Eth-Src", "Eth-Dst", "Idle-TO", "Packets", "Bytes",
        )
        for stat in sorted(flows, key=lambda f: f.priority, reverse=True):
            match   = stat.match
            in_port = match.get("in_port", "*")
            eth_src = match.get("eth_src",  "*")
            eth_dst = match.get("eth_dst",  "*")
            self.logger.info(
                "  %-4s  %-6s  %-22s  %-22s  %-8s  %-10s  %s",
                stat.priority,
                in_port,
                eth_src,
                eth_dst,
                stat.idle_timeout,
                stat.packet_count,
                stat.byte_count,
            )
        self.logger.info(
            "  [Total packets=%d  flooded=%d  forwarded=%d  learned=%d]",
            self._pkt_count, self._flood_count,
            self._fwd_count, self._learn_count,
        )
        self.logger.info("-" * 60)

    # ----------------------------------------------------------------------- #
    #  Helpers                                                                 #
    # ----------------------------------------------------------------------- #

    def _install_flow(self, datapath, priority, match, actions,
                      buffer_id=None, idle_timeout=0, hard_timeout=0):
        """Build and send an OFPFlowMod to install a flow rule."""
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        kwargs = dict(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        )
        if buffer_id is not None and buffer_id != ofproto.OFP_NO_BUFFER:
            kwargs["buffer_id"] = buffer_id

        mod = parser.OFPFlowMod(**kwargs)
        datapath.send_msg(mod)

    def _send_packet(self, datapath, buffer_id, in_port, actions, data):
        """Send an OFPPacketOut (used for flooding or unbuffered forwarding)."""
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        kwargs = dict(
            datapath=datapath,
            buffer_id=buffer_id if buffer_id is not None else ofproto.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
        )
        if buffer_id is None or buffer_id == ofproto.OFP_NO_BUFFER:
            kwargs["data"] = data

        out = parser.OFPPacketOut(**kwargs)
        datapath.send_msg(out)

    def _request_flow_stats(self, datapath):
        """Send a FlowStatsRequest to a given switch."""
        parser  = datapath.ofproto_parser
        ofproto = datapath.ofproto
        req     = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    def _stats_poll_loop(self):
        """Background greenlet: request flow-table stats every N seconds."""
        hub.sleep(5)   # initial delay
        while True:
            for dpid, mac_table in list(self.mac_to_port.items()):
                # We need the datapath object — look it up via Ryu's registry
                from ryu.base.app_manager import lookup_service_brick
                switchset = lookup_service_brick("switches")
                if switchset:
                    for dp in switchset.dps.values():
                        if dp.id == dpid:
                            self._request_flow_stats(dp)
            hub.sleep(self.STATS_POLL_INTERVAL)

    # ----------------------------------------------------------------------- #
    #  Public helper — dump MAC table (callable from CLI / tests)              #
    # ----------------------------------------------------------------------- #

    def get_mac_table(self, dpid=None):
        """Return the MAC-to-port table (all switches or a specific one)."""
        if dpid is not None:
            return self.mac_to_port.get(dpid, {})
        return self.mac_to_port

    def get_counters(self):
        """Return packet counters as a dict."""
        return {
            "total"    : self._pkt_count,
            "flooded"  : self._flood_count,
            "forwarded": self._fwd_count,
            "learned"  : self._learn_count,
        }
