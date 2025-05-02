#!/usr/bin/env python3
"""
network-benchmark.py – Multipurpose harness for the three-zone Matter lab.

• Autodiscovers containers named  <zone>_device*
• Runs ping + iperf3 for every (src,dst) pair
• Optional: --cross-zone-only, --sample N
• Safe if flows are 100 % dropped
• CSV + iptables dump per run
"""

import argparse, concurrent.futures, csv, datetime, json, pathlib, subprocess, sys
import time, random                                 
from subprocess import CalledProcessError
from typing     import Dict, List, Tuple


SCRIPT_VERSION   = "2.5"
PING_COUNT       = 10       # ICMP echo requests per flow
IPERF_SECS       = 5        # seconds per iperf3 run
PARALLEL_STREAMS = 1        # -P argument to iperf3
MAX_WORKERS      = 6        # concurrent flows tested in parallel
ROUTER_NAME      = "router"
PREFIX           = "matter-docker-"                     



def sh(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def discover_devices() -> Dict[str, str]:
    names = sh("docker ps --format '{{.Names}}'").splitlines()
    devices = {}
    for name in names:
        if "_device" in name:                    # crude but effective
            ip = sh(
                f"docker inspect -f "
                f"'{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}} {{{{end}}}}' {name}"
            ).split()[0]
            devices[name] = ip
    if len(devices) < 2:
        raise RuntimeError("No *device* containers found – is the lab up?")
    return devices


def ping(src: str, dst_ip: str, count: int) -> Dict:
    try:
        out = sh(f"docker exec {src} ping -c {count} {dst_ip}")
    except CalledProcessError as e:
        out = e.output if isinstance(e.output, str) else e.output.decode()

    try:
        rtt_line = next(l for l in out.splitlines() if "rtt" in l)
        _, stats = rtt_line.split("=")
        min_, avg, max_, mdev = (float(x) for x in stats.strip().split()[0].split("/"))
    except StopIteration:                       # 100 % loss
        return dict(min_ms=None, avg_ms=None, max_ms=None, mdev_ms=None, loss_pct=100.0)

    loss_line = next(l for l in out.splitlines() if "packet loss" in l)
    loss_pct  = float(loss_line.split("%")[0].split()[-1])
    return dict(min_ms=min_, avg_ms=avg, max_ms=max_, mdev_ms=mdev, loss_pct=loss_pct)


def iperf(src: str, dst_ip: str, secs: int, streams: int, port: int = 5201) -> Dict:
    cmd = (
        f"docker exec {src} iperf3 -J -t {secs} "
        f"-P {streams} -p {port} -c {dst_ip}"
    )
    try:
        js   = json.loads(sh(cmd))
        recv = js["end"]["sum_received"]
        send = js["end"]["sum_sent"]
        return dict(
            bw_mbps     = recv["bits_per_second"] / 1e6,
            retransmits = send.get("retransmits", 0),
        )
    except CalledProcessError:
        return dict(bw_mbps=0.0, retransmits=None)


def test_flow(src: str, dst: str, dst_ip: str,
              ping_cnt: int, iperf_secs: int, streams: int,
              scenario: str) -> Dict:
    ping_stats  = ping(src, dst_ip, ping_cnt)
    iperf_stats = iperf(src, dst_ip, iperf_secs, streams)
    # strip prefix only for recording
    disp_src = src.removeprefix(PREFIX)
    disp_dst = dst.removeprefix(PREFIX)
    return {
        "timestamp":      datetime.datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "script_version": SCRIPT_VERSION,
        "scenario":       scenario,
        "src":            disp_src,
        "dst":            disp_dst,
        "ping_count":     ping_cnt,
        "iperf_secs":     iperf_secs,
        "num_streams":    streams,
        **ping_stats,
        **iperf_stats,
    }


def wait_for_iperf(containers, timeout=10):
    start   = time.time()
    missing = set(containers)
    while missing and (time.time() - start) < timeout:
        for c in list(missing):
            try:
                sh(f"docker exec {c} pgrep iperf3")
                missing.remove(c)
            except CalledProcessError:
                pass
        if missing:
            time.sleep(0.5)
    if missing:
        raise RuntimeError("Timed out waiting for iperf3 on: " + ", ".join(missing))

def fw_reset():  sh(f"docker exec {ROUTER_NAME} iptables -Z")
def fw_dump() -> str:
    return sh(f"docker exec {ROUTER_NAME} iptables -L FORWARD -v -n -x")

def zone_of(name: str) -> str:
    return name.removeprefix(PREFIX).split("_", 1)[0]

def main() -> None:
    pa = argparse.ArgumentParser(description="Matter benchmark harness")
    pa.add_argument("scenario",
                    help="label for this run")
    pa.add_argument("--ping",    type=int, default=PING_COUNT)
    pa.add_argument("--iperf",   type=int, default=IPERF_SECS)
    pa.add_argument("--streams", type=int, default=PARALLEL_STREAMS)
    pa.add_argument("--workers", type=int, default=MAX_WORKERS)
    pa.add_argument("--cross-zone-only", action="store_true",
                    help="only test flows between different zones")
    pa.add_argument("--sample", type=int, default=0,
                    help="randomly pick N flows after optional filters")
    args = pa.parse_args()

    devices = discover_devices()
    print(f"\n=== {args.scenario} | devices: {len(devices)} | v{SCRIPT_VERSION} ===")

    # spin up iperf3 servers
    for c in devices:
        subprocess.Popen(
            f'docker exec -d {c} sh -c "pgrep iperf3 || iperf3 -s -D"',
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    wait_for_iperf(devices.keys())
    fw_reset()

    pairs: List[Tuple[str, str]] = [(s, d)
                                    for s in devices
                                    for d in devices if s != d]

    if args.cross_zone_only:
        pairs = [(s, d) for s, d in pairs if zone_of(s) != zone_of(d)]
        print(f"→ cross-zone flows only: {len(pairs)}")

    if args.sample:
        if args.sample > len(pairs):
            sys.exit(f"❌ sample size {args.sample} exceeds total flows {len(pairs)}")
        pairs = random.sample(pairs, args.sample)
        print(f"→ random sample: {args.sample} flows")

    rows: List[Dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        fut2pair = {pool.submit(test_flow, s, d, devices[d],
                                args.ping, args.iperf, args.streams,
                                args.scenario): (s, d) for s, d in pairs}
        for fut in concurrent.futures.as_completed(fut2pair):
            row = fut.result()
            rows.append(row)
            avg = "—" if row["avg_ms"] is None else f"{row['avg_ms']:.2f}"
            print(f"{row['src']:<25} ➜ {row['dst']:<25} "
                  f"RTT {avg:>6} ms   {row['bw_mbps']:>8.1f} Mb/s  "
                  f"loss {row['loss_pct']:>5.1f}%")

    csv_path = pathlib.Path(f"benchmark_{args.scenario}.csv")
    fw_path  = pathlib.Path(f"fw_{args.scenario}.txt")

    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)

    fw_path.write_text(fw_dump())

    print(f"\nSaved results → {csv_path}")
    print(f"Saved iptables counters → {fw_path}\n")


if __name__ == "__main__":
    main()

