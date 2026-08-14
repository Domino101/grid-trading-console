import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from collections import defaultdict
from typing import Any

CLOUDFLARE_CIDRS: list[str] = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"
]

def is_cloudflare(ip_str: str) -> bool:
    """Check if the given IP address string falls within Cloudflare's CIDR blocks."""
    try:
        ip = ipaddress.ip_address(ip_str)
        for cidr in CLOUDFLARE_CIDRS:
            if ip in ipaddress.ip_network(cidr):
                return True
    except Exception:
        pass
    return False

def get_local_ip() -> str:
    """Retrieve the primary local IP address by attempting to connect to a dummy address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '10.172.0.2'
    finally:
        s.close()
    return IP

def parse_ip_port(s: str) -> tuple[str, int]:
    """Parse tcpdump IP format (e.g. 10.172.0.2.22 or 10.172.0.2) into IP address and port."""
    parts = s.split('.')
    if len(parts) >= 5:
        ip = ".".join(parts[:-1])
        try:
            port = int(parts[-1])
        except ValueError:
            ip = s
            port = 0
    else:
        ip = s
        port = 0
    return ip, port

def main() -> None:
    """Main execution block to set up subprocess, capture packet headers, and aggregate statistics."""
    parser = argparse.ArgumentParser(description="Crypto Trading Bot Network Traffic Auditor")
    parser.add_argument("--duration", type=int, default=180, help="Capture duration in seconds (default: 180)")
    parser.add_argument("--output", type=str, default=None, help="Path to save markdown report (default: print only)")
    parser.add_argument("--raw-output", type=str, default=None, help="Path to save raw JSON details (default: None)")
    args = parser.parse_args()

    local_ip = get_local_ip()
    print(f"Local IP detected: {local_ip}")
    print(f"Starting network traffic capture on interface 'ens4' for {args.duration} seconds...")
    print("Capturing packet details (L3 length) via tcpdump...")

    cmd = ["sudo", "tcpdump", "-l", "-n", "-i", "ens4", "-v"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    start_time = time.time()
    end_time = start_time + args.duration

    traffic = defaultdict(lambda: {"bytes": 0, "packets": 0})

    header_pattern = re.compile(r"IP \(.*length (\d+)\)")
    ip_line_pattern = re.compile(r"^\s+(\S+)\s+>\s+(\S+):")

    current_length = None
    current_proto = "UNKNOWN"
    last_print = time.time()

    try:
        while time.time() < end_time:
            import select
            r, _, _ = select.select([proc.stdout], [], [], 1.0)
            if not r:
                continue

            line = proc.stdout.readline()
            if not line:
                break

            if "proto" in line:
                proto_match = re.search(r"proto (\w+)", line)
                if proto_match:
                    current_proto = proto_match.group(1)

            h_match = header_pattern.search(line)
            if h_match:
                current_length = int(h_match.group(1))
                continue

            if current_length is not None:
                ip_match = ip_line_pattern.match(line)
                if ip_match:
                    src_str = ip_match.group(1)
                    dst_str = ip_match.group(2)

                    src_ip, src_port = parse_ip_port(src_str)
                    dst_ip, dst_port = parse_ip_port(dst_str)

                    key = (src_ip, src_port, dst_ip, dst_port, current_proto)
                    traffic[key]["bytes"] += current_length
                    traffic[key]["packets"] += 1

                    current_length = None
                    current_proto = "UNKNOWN"

            now = time.time()
            if now - last_print >= 10:
                elapsed = now - start_time
                remaining = max(0, args.duration - elapsed)
                total_bytes_seen = sum(info["bytes"] for info in traffic.values())
                print(f"Progress: {elapsed:.1f}s / {args.duration}s | Captured: {total_bytes_seen/1024/1024:.2f} MB")
                last_print = now

    except KeyboardInterrupt:
        print("\nCapture interrupted by user.")
    except Exception as e:
        print(f"\nError in capture loop: {e}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\nCapture completed. Processing and resolving hostnames...")

    ip_resolved: dict[str, str] = {}
    unique_ips: set[str] = set()
    for (src_ip, _, dst_ip, _, _) in traffic.keys():
        unique_ips.add(src_ip)
        unique_ips.add(dst_ip)

    for ip in unique_ips:
        if ip == local_ip:
            ip_resolved[ip] = "Local VM"
            continue
        if ip.startswith("10.") or ip.startswith("172.1") or ip.startswith("192.168.") or ip == "127.0.0.1":
            ip_resolved[ip] = "Internal/Private"
            continue

        if is_cloudflare(ip):
            ip_resolved[ip] = f"Exchange API (Cloudflare IP: {ip})"
            continue

        try:
            socket.setdefaulttimeout(1.0)
            name, _, _ = socket.gethostbyaddr(ip)
            ip_resolved[ip] = name
        except Exception:
            ip_resolved[ip] = f"External IP ({ip})"

    egress_bytes = 0
    ingress_bytes = 0
    total_packets = 0

    by_host: dict[str, dict[str, Any]] = defaultdict(lambda: {"egress": 0, "ingress": 0, "packets": 0})
    by_port: dict[str, dict[str, Any]] = defaultdict(lambda: {"egress": 0, "ingress": 0, "packets": 0})

    for (src_ip, src_port, dst_ip, dst_port, proto), info in traffic.items():
        b = info["bytes"]
        p = info["packets"]
        total_packets += p

        is_egress = False
        if src_ip == local_ip or src_ip.startswith("10."):
            is_egress = True
            egress_bytes += b
            remote_ip = dst_ip
            remote_port = dst_port
            local_port = src_port
        else:
            ingress_bytes += b
            remote_ip = src_ip
            remote_port = src_port
            local_port = dst_port

        host_name = ip_resolved.get(remote_ip, remote_ip)

        if local_port == 22 or remote_port == 22:
            service = "SSH Tunnel (VS Code / Shell)"
        elif local_port in (8000, 8001, 8005, 8006) or remote_port in (8000, 8001, 8005, 8006):
            service = "Web UI / API (Port 8001)"
        elif remote_port == 443 or local_port == 443:
            service = f"Exchange HTTPS / WSS ({host_name})"
        elif remote_port == 53 or local_port == 53:
            service = "DNS Queries (Port 53)"
        else:
            service = f"Other ({proto}:{remote_port})"

        by_host[host_name]["packets"] += p
        by_port[service]["packets"] += p

        if is_egress:
            by_host[host_name]["egress"] += b
            by_port[service]["egress"] += b
        else:
            by_host[host_name]["ingress"] += b
            by_port[service]["ingress"] += b

    total_bytes = egress_bytes + ingress_bytes
    if total_bytes == 0:
        print("No traffic captured. Make sure the bot is running and interface is active.")
        return

    report: list[str] = []
    report.append("# Crypto Trading Bot Network Traffic Diagnostics Report")
    report.append("- **Capture Interface**: `ens4`")
    report.append(f"- **Capture Duration**: {args.duration} seconds")
    report.append(f"- **Local VM IP**: `{local_ip}`")
    report.append(f"- **Timestamp**: `{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC`")
    report.append("")
    report.append("## 1. Traffic Summary")
    report.append("")
    report.append("| Direction | Packets | Bytes | Megabytes (MB) | Proportion |")
    report.append("| :--- | :--- | :--- | :--- | :--- |")
    report.append(f"| **Egress (Outbound)** | {sum(h['packets'] for h in by_host.values() if h['egress'] > 0):,} | {egress_bytes:,} | {egress_bytes/1024/1024:.2f} MB | {egress_bytes/total_bytes*100:.1f}% |")
    report.append(f"| **Ingress (Inbound)** | {sum(h['packets'] for h in by_host.values() if h['ingress'] > 0):,} | {ingress_bytes:,} | {ingress_bytes/1024/1024:.2f} MB | {ingress_bytes/total_bytes*100:.1f}% |")
    report.append(f"| **Total Network Traffic** | {total_packets:,} | {total_bytes:,} | {total_bytes/1024/1024:.2f} MB | 100.0% |")
    report.append("")
    report.append("> [!NOTE]")
    report.append("> Egress traffic refers to data sent from the GCP VM to the internet, which is the main source of network billing fees. Ingress traffic is incoming data and is typically free in GCP.")
    report.append("")

    report.append("## 2. Traffic by Service Type")
    report.append("")
    report.append("| Service Type / Port | Egress (MB) | Ingress (MB) | Total (MB) | Proportion | Packets |")
    report.append("| :--- | :--- | :--- | :--- | :--- | :--- |")

    sorted_ports = sorted(by_port.items(), key=lambda x: (x[1]["egress"] + x[1]["ingress"]), reverse=True)
    for service, info in sorted_ports:
        s_total = info["egress"] + info["ingress"]
        report.append(f"| {service} | {info['egress']/1024/1024:.2f} MB | {info['ingress']/1024/1024:.2f} MB | {s_total/1024/1024:.2f} MB | {s_total/total_bytes*100:.1f}% | {info['packets']:,} |")
    report.append("")

    report.append("## 3. Top Traffic Sources & Destinations")
    report.append("")
    report.append("| Hostname / IP | Egress (MB) | Ingress (MB) | Total (MB) | Proportion | Packets |")
    report.append("| :--- | :--- | :--- | :--- | :--- | :--- |")

    sorted_hosts = sorted(by_host.items(), key=lambda x: (x[1]["egress"] + x[1]["ingress"]), reverse=True)
    for host, info in sorted_hosts[:15]:
        h_total = info["egress"] + info["ingress"]
        report.append(f"| {host} | {info['egress']/1024/1024:.2f} MB | {info['ingress']/1024/1024:.2f} MB | {h_total/1024/1024:.2f} MB | {h_total/total_bytes*100:.1f}% | {info['packets']:,} |")
    report.append("")

    report.append("## 4. Key Findings & Recommendations")
    report.append("")

    ssh_info = by_port["SSH Tunnel (VS Code / Shell)"]
    ssh_total = ssh_info["egress"] + ssh_info["ingress"]
    poly_total = sum((info["egress"] + info["ingress"]) for s, info in by_port.items() if "Exchange" in s)
    dash_total = by_port.get("Web UI / API (Port 8001)", {}).get("egress", 0) + by_port.get("Web UI / API (Port 8001)", {}).get("ingress", 0)

    findings_count = 0
    if ssh_total > total_bytes * 0.4:
        findings_count += 1
        report.append(f"### Finding {findings_count}: High SSH / IDE Tunnel Traffic")
        report.append(f"- **Data usage**: {ssh_total/1024/1024:.2f} MB ({ssh_total/total_bytes*100:.1f}% of total).")
        report.append("- **Description**: A large percentage of the network traffic goes through the SSH port (port 22). This includes your active VS Code IDE session, directory scans, log sync, and any port-forwarded traffic.")
        report.append("- **Recommendation**: Close unused VS Code tabs, reduce log files size, and avoid operations that trigger large workspace file scans.")
        report.append("")

    if poly_total > total_bytes * 0.4:
        findings_count += 1
        report.append(f"### Finding {findings_count}: Heavy Exchange API & Websocket Traffic")
        report.append(f"- **Data usage**: {poly_total/1024/1024:.2f} MB ({poly_total/total_bytes*100:.1f}% of total).")
        report.append("- **Description**: The bot's communication with Exchange REST/WebSocket endpoints is a main driver of egress/ingress traffic.")
        report.append("- **Recommendation**: Check WebSocket stability. If the WebSocket disconnects frequently, it falls back to high-frequency REST polling. Also, review polling rates in config.")
        report.append("")

    if dash_total > total_bytes * 0.05:
        findings_count += 1
        report.append(f"### Finding {findings_count}: Significant Dashboard API Traffic")
        report.append(f"- **Data usage**: {dash_total/1024/1024:.2f} MB ({dash_total/total_bytes*100:.1f}% of total).")
        report.append("- **Description**: FastAPI dashboard endpoints consume bandwidth.")
        report.append("- **Recommendation**: Increase the frontend dashboard auto-refresh interval from 15s to 30s or 60s, or pause refresh completely when leaving the page open.")
        report.append("")

    report.append("### Recommendations to Reduce GCP Egress Costs")
    report.append("1. **Log Rotation**: Capping log sizes reduces network overhead during sync/downloads.")
    report.append("2. **WebSocket Stability**: Ensure the WebSocket stays connected to avoid REST header overhead.")
    report.append("3. **Dashboard Refresh Rate**: Use the frontend dropdown to select a longer refresh rate or pause refresh.")
    report.append("")

    report_text = "\n".join(report)

    print("\n--- REPORT START ---")
    print(report_text)
    print("--- REPORT END ---\n")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(report_text)
        print(f"Report successfully saved to: {args.output}")

    if args.raw_output:
        os.makedirs(os.path.dirname(os.path.abspath(args.raw_output)), exist_ok=True)
        serializable_traffic = []
        for (src_ip, src_port, dst_ip, dst_port, proto), info in traffic.items():
            serializable_traffic.append({
                "src_ip": src_ip, "src_port": src_port,
                "dst_ip": dst_ip, "dst_port": dst_port,
                "proto": proto, "bytes": info["bytes"], "packets": info["packets"]
            })
        with open(args.raw_output, "w") as f:
            json.dump(serializable_traffic, f, indent=2)
        print(f"Raw traffic details saved to: {args.raw_output}")

if __name__ == '__main__':
    main()
