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
from tools.build_native_app import compile_native
from tools.build_jvm import JDK
from tools.prove_sd_app import state,catalog_state
from tools.setup_arm_toolchain import EXECUTABLE
from tools.sd_image import Fat16,create_image
from tools.install_sd_app import install


JAVA_TOOLS=(JDK/'javac.exe').exists()


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

    def fixture(self,program,missing=False,short_read=False,files=None,pool_free=0x180000,budget=500000):
        # This deliberately substitutes SFS/graphics/allocator for fast CPU unit
        # tests. tools.prove_sd_app uses actual firmware APIs and SD/SPI instead.
        uc=Uc(UC_ARCH_ARM,UC_MODE_ARM|UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(UC_CPU_ARM_926)
        uc.mem_map(0,0x800000);uc.mem_write(0,self.image)
        uc.mem_map(0x04000000,0x400000);uc.mem_map(0x40000000,0x10000)
        # Dynamic App Heap as a ThreadX byte pool with one free 1.5 MiB block;
        # only its headers are read, allocations come from the stub below.
        pool_start,pool_size=0x04300000,pool_free
        uc.mem_write(0x042c4d50,struct.pack('>8I',0x42595445,0,0,0,0,0,pool_start,pool_size))
        uc.mem_write(pool_start,struct.pack('>II',pool_start+pool_size,0xffffeeee))
        calls=dict(opens=[],closed=0,freed=[],rects=[],texts=[],allocation_sizes=[],allocated=[],creates=[],heaps=[],timers=[],stopped=[],repaints=[],flushes=[])
        if files is None:
            files={'E:\\APPS.LST':b'DEMO.B22\r\nSECOND.B22\r\n','E:\\DEMO.B22':program,
                   'E:\\SECOND.B22':assemble((ROOT/'apps/card_second.b22asm').read_text())}
        if missing:files.pop('E:\\DEMO.B22',None)
        # Stub allocations start low in RAM, clear of the pool, clock and loader state.
        handles={};positions={};heap_next=0x04000000
        def stub(uc,pc,size,_):
            nonlocal heap_next
            args=[uc.reg_read(UC_ARM_REG_R0+i) for i in range(4)]
            r0,r1,r2,r3=args;result=0
            self.assertEqual(uc.reg_read(UC_ARM_REG_SP)&7,0)
            if pc==0x5edc5e:
                name=bytes(uc.mem_read(r0,32)).decode('utf-16-be').split('\0',1)[0]
                calls['opens'].append((name,r1))
                if name in files:
                    result=len(handles)+1;handles[result]=name;positions[result]=0
            elif pc==0x5ee59e:uc.mem_write(r1,len(files[handles[r0]]).to_bytes(4,'big'))
            elif pc==0x6a5438:
                calls['allocation_sizes'].append(r0);calls['heaps'].append(r1);result=heap_next
                calls['allocated'].append(result);heap_next+=(r0+7)&~7
            elif pc==0x5ee49e:
                data=files[handles[r0]];at=positions[r0]
                chunk=data[at:at+r2]
                if short_read and handles[r0]=='E:\\DEMO.B22':chunk=chunk[:-1]
                positions[r0]=at+len(chunk)
                uc.mem_write(r1,chunk);uc.mem_write(r3,len(chunk).to_bytes(4,'big'))
            elif pc==0x5ee526:  # SFS_SetFilePointer(handle, high, low, origin 0)
                self.assertEqual((r1,r3),(0,0))
                positions[r0]=r2
            elif pc==0x5ee484:calls['closed']+=1
            elif pc==0x6a5468:calls['freed'].append(r0)
            elif pc==0x4c65ac:calls['rects'].append((r1,r2,r3))
            elif pc==0x4cf432:
                count=int.from_bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_SP),4),'big')
                calls['texts'].append(bytes(uc.mem_read(r3,count*2)).decode('utf-16-be'))
            elif pc==0x4d7da2:calls['creates'].append((r0,r1));result=1
            elif pc==0x1f55ee:calls['timers'].append((r0,r1,r2));result=7
            elif pc==0x1f5640:calls['stopped'].append(r0);result=1
            elif pc==0x4d67d2:calls['repaints'].append(r0);result=1
            elif pc==0x1d081e:calls['flushes'].append((r1,r2))
            uc.reg_write(UC_ARM_REG_R0,result)
            uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
        for pc in (0x60f74e,0x4c5b68,0x5edc5e,0x5ee59e,0x6a5438,0x5ee49e,0x5ee484,0x6a5468,0x4c65ac,0x4cf432,0x4d7da2,
                   0x1f55ee,0x1f5640,0x4d67d2,0x1d081e,0x5ee526):
            uc.hook_add(UC_HOOK_CODE,stub,begin=pc,end=pc)
        class Guest:pass
        guest=Guest();guest.uc=uc
        def event(msg,entrypoint=None,param=0):
            uc.reg_write(UC_ARM_REG_CPSR,0x2f3)
            uc.reg_write(UC_ARM_REG_SP,0x4000e000)
            uc.reg_write(UC_ARM_REG_LR,0x4000f001)
            uc.reg_write(UC_ARM_REG_R0,0x160002)
            uc.reg_write(UC_ARM_REG_R0+1,msg)
            uc.reg_write(UC_ARM_REG_R0+2,param)
            if msg==0xf020:
                uc.mem_write(param,bytes([7]))  # stub timer id
                clock=int.from_bytes(uc.mem_read(0x042ccec0,4),'big')+20  # 20 ms per tick
                uc.mem_write(0x042ccec0,clock.to_bytes(4,'big'))
            uc.emu_start(entrypoint or self.entry,0x4000f000,count=budget)
            self.assertEqual(uc.reg_read(UC_ARM_REG_PC),0x4000f000)
            self.assertEqual(uc.reg_read(UC_ARM_REG_SP),0x4000e000)
            result=state(guest);result['catalog_state']=catalog_state(guest)
            return result
        return event,calls

    def test_every_allocation_uses_the_app_heap(self):
        # Any other heap type makes a large request assert and reboot the phone.
        event,calls=self.fixture(assemble((ROOT/'apps/card_demo.b22asm').read_text()))
        event(0xf021);event(0xfa1b);event(0xfa17)
        self.assertTrue(calls['heaps'])
        self.assertEqual(set(calls['heaps']),{0x44444444})

    def test_native_program_runs_from_the_heap_and_returns_to_list(self):
        program=compile_native(ROOT/'apps/card_native.c')
        event,calls=self.fixture(program)
        event(0xf021)
        loaded=event(0xfa1b)
        self.assertEqual((loaded['mode'],loaded['status']),(2,0))
        self.assertIn(1<<20,calls['allocation_sizes'])
        self.assertEqual(set(calls['heaps']),{0x44444444})
        for line in ('NATIVE B22N','HEAP 1024 KB','APPS.LST 22','KEYS 0'):
            self.assertIn(line,calls['texts'])
        event(0xfa04);event(0xfa04);event(0xfa02)
        self.assertIn('KEYS 3',calls['texts'])
        self.assertEqual(calls['rects'][-1],(((84)<<16)|94,((107)<<16)|117,0x07ff))
        done=event(0xfa1b)
        self.assertEqual((done['mode'],done['code'],done['status']),(0,0,0))
        event(0xf022)
        self.assertEqual(sorted(calls['freed']),sorted(calls['allocated']))

    def test_allocation_that_does_not_fit_is_refused_before_the_firmware(self):
        program=compile_native(ROOT/'apps/card_native.c')
        event,calls=self.fixture(program,pool_free=0x80000)
        event(0xf021);loaded=event(0xfa1b)
        self.assertEqual(loaded['mode'],2)
        self.assertNotIn(1<<20,calls['allocation_sizes'])
        self.assertIn('HEAP FAILED',calls['texts'])
        event,calls=self.fixture(program,pool_free=0x400)
        event(0xf021);self.assertEqual(event(0xfa1b)['status'],3)

    def test_native_timer_screen_flush_and_repaint(self):
        program=compile_native(ROOT/'apps/card_screen.c')
        event,calls=self.fixture(program)
        event(0xf021);event(0xfa1b)
        self.assertEqual(calls['flushes'][-1],(0,(175<<16)|219))
        self.assertEqual(calls['timers'],[(0x160002,500,1)])
        uc_ticks=event(0xf020,param=0x40000100)  # parameter points to the timer id byte
        self.assertEqual(uc_ticks['mode'],2)
        event(0xfa1b)
        self.assertEqual(calls['stopped'],[7])
        self.assertEqual(calls['repaints'],[2])

    def test_native_program_gets_soft_keys_and_long_right_soft_returns(self):
        event,calls=self.fixture(compile_native(ROOT/'apps/card_native.c'))
        event(0xf021);event(0xfa1b)
        for message in (0xfa16,0xfa17,0xfb17,0xfe01,0xfd01):
            result=event(message)
            self.assertEqual((result['mode'],result['event']),(2,message))
        back=event(0xfc17)
        self.assertEqual((back['mode'],back['code']),(0,0))
        self.assertEqual(calls['repaints'],[2])

    @unittest.skipUnless(JAVA_TOOLS,'Local JDK required')
    def test_java_vm_runs_a_program_from_jars(self):
        from tools.build_java_phone import build_app,build_vm,zip_directory
        from tools.build_jvm import LIBRARY,build_library
        build_library()
        with TemporaryDirectory() as folder:
            library=Path(folder)/'JAVALIB.JAR'
            zip_directory(LIBRARY,library)
            files={'E:\\APPS.LST':b'JAVA.B22\r\n','E:\\JAVA.B22':build_vm(),
                   'E:\\JAVALIB.JAR':library.read_bytes(),
                   'E:\\APP.JAR':build_app([ROOT/'apps/java/Hello.java'],'Hello')}
        event,calls=self.fixture(b'',files=files,budget=400000000)
        event(0xf021);loaded=event(0xfa1b)
        self.assertEqual((loaded['mode'],loaded['status']),(2,0))
        self.assertEqual(calls['timers'],[(0x160002,20,1)])
        for _ in range(200):
            event(0xf020,param=0x40000100)
            if '[finished]' in calls['texts']:break
        for line in ('B220 + B22 JVM','fib(15) = 610','[n0, n1, n4, n9, n16]','thread sum 6','slept true','[finished]'):
            self.assertIn(line,calls['texts'])
        event(0xfa1b);event(0xf022)  # OK after the end exits; closing frees everything
        self.assertEqual(sorted(calls['freed']),sorted(calls['allocated']))
        self.assertEqual(calls['closed'],len([o for o in calls['opens'] if o[0]!='E:\\MISSING']))

    def test_invalid_native_programs_are_rejected(self):
        program=compile_native(ROOT/'apps/card_native.c')
        length=struct.unpack_from('>I',program,8)[0]
        broken=[program[:-1],bytearray(program),bytearray(program),bytearray(program)]
        struct.pack_into('>I',broken[1],16,0)                 # ARM-mode entry
        struct.pack_into('>I',broken[2],32+length,2)          # unaligned relocation
        struct.pack_into('>I',broken[3],32+length,length)     # relocation outside the image
        for data in broken:
            with self.subTest(data=len(data)):
                event,calls=self.fixture(bytes(data))
                event(0xf021);result=event(0xfa1b)
                self.assertEqual((result['status'],result['code']),(5,0))
                event(0xf022)
                self.assertEqual(sorted(calls['freed']),sorted(calls['allocated']))

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
