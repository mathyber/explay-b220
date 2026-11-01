"""Run the original SIM-dependent WAP provisioning routine in an isolated CPU.

The experiment currently supplies SIM/MN services through HLE. Operator account
formats and defaults come from the PS, not host-authored browser workarounds.
Stop before the routine queues an NV write; copy only its WAP configuration.
"""
import hashlib

from unicorn.arm_const import UC_ARM_REG_CPSR,UC_ARM_REG_R0,UC_ARM_REG_SP,UC_ARM_REG_LR
from .board import Board
from .network_trace import PS_SHA256

BASE,SIZE = 0x0433613c,0x43b0


def provision_wap_accounts(board):
    if board.image_sha256 != PS_SHA256:
        raise ValueError('SIM provisioning routine requires the pinned B220 PS')
    isolated = Board(profile=board.profile,explore=board.explore)
    isolated.uc.mem_write(0x04000000,bytes(board.ram))
    isolated.uc.reg_write(UC_ARM_REG_CPSR,0x2f3)
    isolated.uc.reg_write(UC_ARM_REG_R0,0)
    isolated.uc.reg_write(UC_ARM_REG_SP,0x4000f000)
    isolated.uc.reg_write(UC_ARM_REG_LR,0x4000fff0)
    result = isolated.run(1000000,10,entry=0x598615,until=0x598e18,report=False)
    if result['stop']['reason'] != 'requested end address':
        raise ValueError(f'Original SIM provisioning did not finish preparing settings: {result["stop"]}')
    settings = bytes(isolated.uc.mem_read(BASE,SIZE))
    return settings,dict(routine='MMIBRW_UpdateNetworkAccount',entry='0x598614',
                         stopped_before_nv_write='0x598e18',sha256=hashlib.sha256(settings).hexdigest(),
                         account_count=settings[0x43aa],connection_type=settings[0x24],
                         apn=settings[0xb4:0xd3].split(b'\0',1)[0].decode('ascii',errors='replace'))
