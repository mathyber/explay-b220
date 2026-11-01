"""Replay phone keys from an official snapshot and collect network observations.

Example: python -m tools.network_probe --load-state home.b220state
         --steps soft_left,right,right,ok,wait,down,ok --out analysis/probe
Each step runs a finite CPU budget; 'wait' only advances the CPU.
This tool never saves over the input snapshot or writes the source firmware.
"""
import argparse
import json
from pathlib import Path

from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_CPSR
from emulator.keypad import KEY_MATRIX
from emulator.network_trace import NetworkTrace
from emulator.snapshot import load_snapshot,save_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--load-state',type=Path,required=True)
    parser.add_argument('--steps',required=True,help='Comma-separated phone keys or wait; no automatic wake key')
    parser.add_argument('--out',type=Path,required=True,help='New, empty output directory')
    parser.add_argument('--instructions',type=int,default=8000000,help='CPU budget per step')
    parser.add_argument('--seconds',type=float,default=45,help='Wall time limit per step')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--virtual-network',action='store_true',help='Experimental SIM1/PDP setup; stop before DNS; no host network and no output snapshots')
    modes.add_argument('--http-test',action='store_true',help='Route a.aa:80 to a private loopback HTTP fixture; no external network or output snapshots')
    modes.add_argument('--internet',action='store_true',help='Real IPv4 DNS and raw TCP via experimental PDP HLE; preserve phone WAP settings and TLS')
    parser.add_argument('--network-byte-limit',type=int,default=16*1024*1024,help='Emulator byte budget for --internet, not a phone capability limit')
    parser.add_argument('--internet-host',action='append',help='Optional scope restriction for this run; repeat for multiple DNS names')
    parser.add_argument('--internet-port',type=int,action='append',help='Optional TCP port scope restriction for this run')
    parser.add_argument('--sim-plmn',help='Optional virtual SIM home MCC+MNC; initialize stock operator accounts using original firmware code')
    parser.add_argument('--url',help='Explicit one-shot debugger input at the browser URL handoff; requires a URL-editor snapshot and a network mode')
    parser.add_argument('--http-test-domain',default='a.aa',help='Static DNS name used only by the local HTTP fixture')
    args = parser.parse_args()
    steps = args.steps.split(',')
    service_steps = ('sim_remove','sim_insert','register','unregister')
    if any(key not in (*KEY_MATRIX,'end','wait',*service_steps) for key in steps):
        parser.error('Unknown step; use a phone key name or wait')
    if args.instructions <= 0 or not 0 < args.seconds < float('inf'):
        parser.error('Budgets must be positive and finite')
    if args.url and not (args.virtual_network or args.http_test or args.internet):
        parser.error('--url requires an experimental network mode')
    if (args.sim_plmn or any(key in service_steps for key in steps)) and not (args.virtual_network or args.http_test or args.internet):
        parser.error('SIM service commands require an experimental network mode')
    if not 1 <= args.network_byte_limit <= 256*1024*1024:
        parser.error('--network-byte-limit must be 1..256 MiB')
    if args.internet_port and any(not 1 <= port <= 65535 for port in args.internet_port):
        parser.error('--internet-port must be 1..65535')
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        parser.error('--out must be a new or empty directory')
    board = load_snapshot(args.load_state)
    args.out.mkdir(parents=True,exist_ok=True)
    trace = NetworkTrace(board)
    network = None
    backend = fixture = url_input = None
    try:
        if args.virtual_network or args.http_test or args.internet:
            from emulator.virtual_network import VirtualNetwork
            try:
                network = VirtualNetwork(board,stop_at_dns=not (args.http_test or args.internet),
                                         configure_http=args.http_test,home_plmn=args.sim_plmn)
            except ValueError as exc:
                parser.error(str(exc))
            trace.mark('virtual-network-enabled')
            if args.http_test:
                from tools.http_fixture import HttpFixture
                from emulator.host_network import LoopbackNetwork
                fixture = HttpFixture()
                backend = LoopbackNetwork(board,fixture.endpoint,domain=args.http_test_domain)
            elif args.internet:
                from emulator.host_network import InternetNetwork
                backend = InternetNetwork(board,network,byte_limit=args.network_byte_limit,
                                          allowed_hosts=args.internet_host,allowed_ports=args.internet_port)
            if args.url:
                from tools.browser_url_input import BrowserUrlInput
                url_input = BrowserUrlInput(board,args.url)
        for index,key in enumerate(steps,1):
            name = f'{index:02d}-{key}'
            trace.mark(name)
            if key in service_steps:
                if key == 'sim_remove': network.set_service(sim_present=False)
                elif key == 'sim_insert': network.set_service(sim_present=True,registered=False)
                elif key == 'register': network.set_service(registered=True)
                else: network.set_service(registered=False)
            elif key != 'wait':
                board.keypad.key(key,True)
                board.keypad.key(key,False)
            pc = board.uc.reg_read(UC_ARM_REG_PC)
            entry = pc | (1 if board.uc.reg_read(UC_ARM_REG_CPSR)&0x20 else 0)
            result = board.run(args.instructions,args.seconds,entry=entry,report=False)
            board.lcd.save(args.out,name)
            if not network:
                save_snapshot(board,args.out/(name+'.b220state'))
            else:
                (args.out/'virtual-network.json').write_text(json.dumps(network.report(),indent=2),encoding='utf-8')
                (args.out/'wap-settings.bin').write_bytes(bytes(board.uc.mem_read(0x0433613c,0x43b0)))
                if backend:
                    (args.out/'host-network.json').write_text(json.dumps(backend.report(),indent=2),encoding='utf-8')
                    if fixture:
                        (args.out/'http-server.json').write_text(json.dumps(fixture.report(),indent=2),encoding='utf-8')
                if url_input:
                    (args.out/'url-input.json').write_text(json.dumps(url_input.report(),indent=2),encoding='utf-8')
            trace.write(args.out/'network-trace.json')
            print(json.dumps(dict(step=name,stop=result['stop'],backlight=board.lcd_backlight,
                                  counts=dict(trace.counts))),flush=True)
            if result['stop']['reason'] not in ('instruction budget','wall time budget'):
                break
    finally:
        if url_input:
            url_input.close()
        if backend:
            backend.close()
        if fixture:
            fixture.close()
        if network:
            network.close()
        trace.close()


if __name__ == '__main__':
    main()
