"""B22V tests. Synthetic native-API fixtures are separate from cold SD proofs."""
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_MODE_BIG_ENDIAN,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_LR,UC_ARM_REG_SP,UC_ARM_REG_R0,UC_ARM_REG_CPSR,UC_CPU_ARM_926
from emulator.board import ROOT
from tools.b22asm import assemble
from tools.build_catch_game import compile_game
from tools.build_sd_app import build
from tools.prove_sd_app import state,catalog_state
from tools.setup_arm_toolchain import EXECUTABLE
from tools.sd_image import Fat16,create_image
from tools.install_sd_app import install


class AssemblerTests(unittest.TestCase):
    def test_labels_and_negative_immediates_and_ascii_pool(self):
        program=assemble('set r0 -4\nagain: text r0 r1 "HELLO"\nyield again')
        self.assertEqual(struct.unpack('>4sHHHHI',program[:16]),(b'B22V',1,16,3,0,6))
        self.assertEqual(struct.unpack('>I',program[20:24])[0],0xfffffffc)
        self.assertEqual(struct.unpack('>I',program[36:40])[0],1)
        self.assertTrue(program.endswith(b'HELLO\0'))

    def test_invalid_programs_are_rejected(self):
        for source in ('', 'set r8 0', 'jmp nowhere', 'yield 2', 'rect r4', 'clear 65536',
                       'foo r0', 'text r0 r1 "'+('X'*65)+'"', 'a: halt\na: halt',
                       'set r0 0x100000000'):
            with self.subTest(source=source),self.assertRaises(ValueError):assemble(source)


class CardInstallTests(unittest.TestCase):
    def test_installing_two_named_apps_keeps_both_in_catalog(self):
        with TemporaryDirectory() as folder:
            card=Path(folder)/'card.img'
            install(ROOT/'apps/card_demo.b22asm',card)
            install(ROOT/'apps/card_second.b22asm',card)
            volume=Fat16(card.read_bytes())
            entries={e['name']:e for e in volume.entries()}
            self.assertEqual(set(entries),{'DEMO.B22','SECOND.B22','APPS.LST'})
            for name,source in (('DEMO.B22','card_demo.b22asm'),('SECOND.B22','card_second.b22asm')):
                e=entries[name];offset=volume.data+(e['cluster']-2)*volume.cluster_bytes
                self.assertEqual(volume.image[offset:offset+e['size']],assemble((ROOT/'apps'/source).read_text()))
            e=entries['APPS.LST'];offset=volume.data+(e['cluster']-2)*volume.cluster_bytes
            self.assertEqual(volume.image[offset:offset+e['size']],b'DEMO.B22\r\nSECOND.B22\r\n')

    def test_replacing_application_keeps_other_files_and_reclaims_old_chain(self):
        volume=Fat16(create_image())
        volume.add_file('APP.B22',b'a'*2200)
        volume.add_file('KEEP.DAT',b'keep me')
        keep=next(e for e in volume.entries() if e['name']=='KEEP.DAT')
        keep_offset=volume.data+(keep['cluster']-2)*volume.cluster_bytes
        unchanged=bytes(volume.image[keep_offset:keep_offset+volume.cluster_bytes])
        def allocated():
            return sum(volume.image[volume.fat+c*2:volume.fat+c*2+2]!=b'\0\0' for c in range(2,volume.clusters+2))
        self.assertEqual(allocated(),4)
        for _ in range(3):
            volume.replace_file('APP.B22',b'b'*12)
            self.assertEqual(allocated(),2)
            volume.replace_file('APP.B22',b'c'*2200)
            self.assertEqual(allocated(),4)
        self.assertEqual(bytes(volume.image[keep_offset:keep_offset+volume.cluster_bytes]),unchanged)
        self.assertEqual(len(volume.entries()),2)
        original=bytes(volume.image)
        with self.assertRaises(ValueError):volume.replace_file('APP.B22',b'x'*len(volume.image))
        self.assertEqual(volume.image,original)

    def test_corrupt_old_chain_is_not_overwritten(self):
        volume=Fat16(create_image());volume.add_file('APP.B22',b'a')
        cluster=volume.entries()[0]['cluster']
        volume.image[volume.fat+cluster*2:volume.fat+cluster*2+2]=cluster.to_bytes(2,'little')
        original=bytes(volume.image)
        with self.assertRaises(ValueError):volume.replace_file('APP.B22',b'new')
        self.assertEqual(volume.image,original)

    def test_installer_ensure_keeps_user_application_then_explicit_install_changes_it(self):
        with TemporaryDirectory() as folder:
            card=Path(folder)/'card.img';source=Path(folder)/'custom.b22asm'
            source.write_text('halt',encoding='utf-8')
            self.assertEqual(install(source,card),'created')
            original=card.read_bytes()
            source.write_text('clear 65535\nhalt',encoding='utf-8')
            self.assertEqual(install(source,card,ensure=True),'kept')
            self.assertEqual(card.read_bytes(),original)
            self.assertEqual(install(source,card),'installed')
            volume=Fat16(card.read_bytes());entry=volume.entries()[0]
            offset=volume.data+(entry['cluster']-2)*volume.cluster_bytes
            self.assertEqual(volume.image[offset:offset+entry['size']],assemble(source.read_text()))


@unittest.skipUnless(EXECUTABLE.exists(),'Pinned ARM compiler is not installed')
class NativeVmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original=next((ROOT/'analysis/extracted').glob('SC*.bin')).read_bytes()
        with TemporaryDirectory() as folder:
            payload,cls.entry,_=compile_game(Path(folder),'patches/sd_app.c')
        cls.image,cls.manifest=build(original,payload,cls.entry)

    def fixture(self,program,missing=False,short_read=False,files=None):
        # This deliberately substitutes SFS/graphics/allocator for fast CPU unit
        # tests. tools.prove_sd_app uses actual firmware APIs and SD/SPI instead.
        uc=Uc(UC_ARCH_ARM,UC_MODE_ARM|UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(UC_CPU_ARM_926)
        uc.mem_map(0,0x800000);uc.mem_write(0,self.image)
        uc.mem_map(0x04000000,0x400000);uc.mem_map(0x40000000,0x10000)
        calls=dict(opens=[],closed=0,freed=[],rects=[],texts=[],allocation_sizes=[],allocated=[],creates=[])
        if files is None:
            files={'E:\\APPS.LST':b'DEMO.B22\r\nSECOND.B22\r\n','E:\\DEMO.B22':program,
                   'E:\\SECOND.B22':assemble((ROOT/'apps/card_second.b22asm').read_text())}
        if missing:files.pop('E:\\DEMO.B22',None)
        handles={};heap_next=0x04200000
        def stub(uc,pc,size,_):
            nonlocal heap_next
            args=[uc.reg_read(UC_ARM_REG_R0+i) for i in range(4)]
            r0,r1,r2,r3=args;result=0
            self.assertEqual(uc.reg_read(UC_ARM_REG_SP)&7,0)
            if pc==0x5edc5e:
                name=bytes(uc.mem_read(r0,32)).decode('utf-16-be').split('\0',1)[0]
                calls['opens'].append((name,r1))
                if name in files:
                    result=len(handles)+1;handles[result]=name
            elif pc==0x5ee59e:uc.mem_write(r1,len(files[handles[r0]]).to_bytes(4,'big'))
            elif pc==0x6a5438:
                calls['allocation_sizes'].append(r0);result=heap_next
                calls['allocated'].append(result);heap_next+=(r0+7)&~7
            elif pc==0x5ee49e:
                data=files[handles[r0]];self.assertEqual(r2,len(data))
                actual=len(data)-(1 if short_read and handles[r0]=='E:\\DEMO.B22' else 0)
                uc.mem_write(r1,data[:actual]);uc.mem_write(r3,actual.to_bytes(4,'big'))
            elif pc==0x5ee484:calls['closed']+=1
            elif pc==0x6a5468:calls['freed'].append(r0)
            elif pc==0x4c65ac:calls['rects'].append((r1,r2,r3))
            elif pc==0x4cf432:
                count=int.from_bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_SP),4),'big')
                calls['texts'].append(bytes(uc.mem_read(r3,count*2)).decode('utf-16-be'))
            elif pc==0x4d7da2:calls['creates'].append((r0,r1));result=1
            uc.reg_write(UC_ARM_REG_R0,result)
            uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
        for pc in (0x60f74e,0x4c5b68,0x5edc5e,0x5ee59e,0x6a5438,0x5ee49e,0x5ee484,0x6a5468,0x4c65ac,0x4cf432,0x4d7da2):
            uc.hook_add(UC_HOOK_CODE,stub,begin=pc,end=pc)
        class Guest:pass
        guest=Guest();guest.uc=uc
        def event(msg,entrypoint=None):
            uc.reg_write(UC_ARM_REG_CPSR,0x2f3)
            uc.reg_write(UC_ARM_REG_SP,0x4000e000)
            uc.reg_write(UC_ARM_REG_LR,0x4000f001)
            uc.reg_write(UC_ARM_REG_R0,0x160002)
            uc.reg_write(UC_ARM_REG_R0+1,msg)
            uc.reg_write(UC_ARM_REG_R0+2,0)
            uc.emu_start(entrypoint or self.entry,0x4000f000,count=500000)
            self.assertEqual(uc.reg_read(UC_ARM_REG_PC),0x4000f000)
            self.assertEqual(uc.reg_read(UC_ARM_REG_SP),0x4000e000)
            result=state(guest);result['catalog_state']=catalog_state(guest)
            return result
        return event,calls

    def test_original_launch_entry_tail_calls_direct_game_window(self):
        event,calls=self.fixture(assemble('halt'))
        event(0,0x60eee9)
        self.assertEqual(calls['creates'],[(0x70d150,0)])

    def test_external_program_handles_events_and_frees_on_close(self):
        program=assemble((ROOT/'apps/card_demo.b22asm').read_text())
        event,calls=self.fixture(program)
        selector=event(0xf021)
        self.assertEqual(selector['catalog_state']['names'],['DEMO.B22','SECOND.B22'])
        first=event(0xfa1b)
        self.assertEqual((first['status'],first['r0'],first['r1']),(0,72,80))
        self.assertEqual(event(0xfa04)['r0'],76)
        self.assertEqual(event(0xfa02)['r1'],84)
        reset=event(0xfa1b)
        self.assertEqual((reset['r0'],reset['r1']),(72,80))
        self.assertIn('FROM SD CARD',calls['texts'])
        self.assertEqual(calls['closed'],2)
        self.assertEqual(event(0xf022)['code'],0)
        self.assertEqual(sorted(calls['freed']),sorted(calls['allocated']))

    def test_missing_truncated_and_invalid_file_return_without_running(self):
        valid=assemble('halt')
        for data,missing,short,status in ((valid,True,False,1),(b'x',False,False,2),
                                          (valid,False,True,4),(b'BAD!'+valid[4:],False,False,5)):
            with self.subTest(status=status):
                event,calls=self.fixture(data,missing,short)
                event(0xf021);result=event(0xfa1b)
                self.assertEqual(result['status'],status)
                self.assertEqual(result['code'],0)
                self.assertEqual(calls['closed'],1 if missing else 2)

    def test_vm_budget_invalid_op_and_bounds(self):
        event,calls=self.fixture(assemble('forever: jmp forever'))
        event(0xf021);result=event(0xfa1b)
        self.assertEqual((result['status'],result['steps']),(8,512))
        for offset,value in ((16,255),(17,8),(19,1),(23,99)):
            invalid=bytearray(assemble('jmp 0'));invalid[offset]=value
            event,_=self.fixture(bytes(invalid))
            event(0xf021);self.assertEqual(event(0xfa1b)['status'],5)
        event,calls=self.fixture(assemble('set r0 170\nset r1 170\nset r2 50\nset r3 50\nset r4 65535\nrect r0\nhalt'))
        event(0xf021);self.assertEqual(event(0xfa1b)['status'],9)
        self.assertEqual(calls['rects'][-1],((170<<16)|170,(175<<16)|175,65535))

    def test_switching_two_apps_and_refresh_releases_all_allocations(self):
        event,calls=self.fixture(assemble((ROOT/'apps/card_demo.b22asm').read_text()))
        event(0xf021);first=event(0xfa1b)
        self.assertEqual(first['mode'],1)
        menu=event(0xfa17)
        self.assertEqual((menu['mode'],menu['code']),(0,0))
        self.assertEqual(event(0xfa02)['catalog_state']['selected'],1)
        second=event(0xfa1b)
        self.assertEqual((second['r0'],second['r1']),(48,64))
        self.assertNotEqual(first['checksum'],second['checksum'])
        event(0xfa17);event(0xfa16);closed=event(0xf022)
        self.assertEqual(closed['catalog'],0)
        self.assertEqual(sorted(calls['freed']),sorted(calls['allocated']))
        self.assertIn((0,(175<<16)|195,0),calls['rects'])

    def test_selector_pages_and_rejects_invalid_catalog_paths(self):
        names=[f'APP{i:02d}.B22' for i in range(12)]
        event,calls=self.fixture(b'',files={'E:\\APPS.LST':('\r\n'.join(names)+'\r\n').encode()})
        event(0xf021)
        for _ in range(11):result=event(0xfa02)
        self.assertEqual(result['catalog_state']['selected'],11)
        self.assertIn('APP11.B22',calls['texts'])
        for raw in (b'..\\X.B22\n',b'123456789.B22\n',b'DEMO.BIN\n'):
            event,_=self.fixture(b'',files={'E:\\APPS.LST':raw})
            result=event(0xf021)
            self.assertEqual((result['status'],result['catalog_state']['count']),(5,0))


if __name__=='__main__':unittest.main()
