// Actual Ghidra decompilation; all descriptive names are research annotations.
// @category B220
import java.io.*;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.data.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.SourceType;

public class B220Pilot extends GhidraScript {
    private static class Seed {
        long start,end; boolean thumb; String name; String[] args;
        Seed(long a,long b,boolean t,String n,String... p) {start=a;end=b;thumb=t;name=n;args=p;}
    }
    private final Seed[] seeds={
        new Seed(0x5b870c,0x5b8725,true,"b220_font_height","font_id"),
        new Seed(0x5b862c,0x5b86bf,true,"b220_select_font_tables","tables","font_id","unicode"),
        new Seed(0x5edc5e,0x5edd09,true,"b220_sfs_open","utf16_path","flags","reserved2","reserved3"),
        new Seed(0x5ee49e,0x5ee4cf,true,"b220_sfs_read","handle","buffer","size","actual","async"),
        new Seed(0x5ee59e,0x5ee5bf,true,"b220_sfs_size","handle","size_out"),
        new Seed(0x5ee484,0x5ee49d,true,"b220_sfs_close","handle"),
        new Seed(0x60eee8,0x60eefb,true,"b220_sokoban_launch_original"),
        new Seed(0x60f74e,0x60f875,true,"b220_sokoban_window_original","window","message","parameter"),
        new Seed(0x527728,0x5278ff,false,"b220_rgb565_rle_row")
    };
    private void block(String name,long base,int size) throws Exception {
        MemoryBlock b=currentProgram.getMemory().createInitializedBlock(name,toAddr(base),size,(byte)0,monitor,false);
        b.setRead(true);b.setWrite(true);b.setExecute(true);
    }
    private void copy(long source,long target,int size) throws Exception {
        byte[] data=new byte[size];currentProgram.getMemory().getBytes(toAddr(source),data);
        currentProgram.getMemory().setBytes(toAddr(target),data);
    }
    private void label(long address,String name) throws Exception {createLabel(toAddr(address),name,true);}
    @Override public void run() throws Exception {
        String[] args=getScriptArgs();
        if(args.length!=1)throw new IllegalArgumentException("Expected export directory");
        Path output=Paths.get(args[0]);Files.createDirectories(output);
        MemoryBlock rom=currentProgram.getMemory().getBlock(toAddr(0));
        rom.setName("PS_ROM");rom.setWrite(false);rom.setExecute(true);
        block("RAM",0x04000000,0x400000);block("SRAM",0x40000000,0x10000);
        copy(0x7237cc,0x04293600,0x2001c);copy(0x74432c,0x40003400,0x364);
        label(0x04388f3c,"b220_stock_sokoban_globals");
        label(0x04388f50,"b220_stock_board_reused_by_our_apps");
        label(0x6d46c,"b220_scrollbar_bitmap_BE_RLE");
        label(0x14c528,"b220_builtin_wallpaper_slot_reused_by_our_patches");
        label(0x6eb6f4,"b220_latin16_widths");label(0x6eb784,"b220_latin16_offsets");label(0x6eb9c4,"b220_latin16_pixels");
        label(0x6ecbb0,"b220_cyrillic16_widths");label(0x6eccb0,"b220_cyrillic16_offsets");label(0x6ed0b0,"b220_cyrillic16_pixels");
        label(0x70d150,"b220_stock_game_window_descriptor");
        label(0x70d108,"b220_stock_sokoban_menu_descriptor");
        ArrayList<Function> functions=new ArrayList<>();
        for(Seed s:seeds) {
            Address start=toAddr(s.start);AddressSet body=new AddressSet(start,toAddr(s.end));
            currentProgram.getProgramContext().setValue(currentProgram.getRegister("TMode"),start,toAddr(s.end),s.thumb?BigInteger.ONE:BigInteger.ZERO);
            DisassembleCommand command=new DisassembleCommand(start,body,true);
            if(!command.applyTo(currentProgram,monitor))throw new IOException("Disassembly failed: "+s.name);
            InstructionIterator epilogues=currentProgram.getListing().getInstructions(body,true);
            while(epilogues.hasNext()) {
                Instruction last=epilogues.next();Instruction previous=last.getPrevious();
                if(s.thumb && last.getMnemonicString().equalsIgnoreCase("bx") &&
                   last.getDefaultOperandRepresentation(0).equalsIgnoreCase("r3") && previous!=null &&
                   previous.getMnemonicString().equalsIgnoreCase("pop") && previous.getDefaultOperandRepresentation(0).equalsIgnoreCase("{r3}")) {
                    last.setFlowOverride(FlowOverride.RETURN);
                    last.setComment(CodeUnit.EOL_COMMENT,"Return: preceding POP restores the saved LR into R3");
                }
            }
            Function f=currentProgram.getFunctionManager().createFunction(s.name,start,body,SourceType.USER_DEFINED);
            f.setCallingConvention("__stdcall");f.setStackPurgeSize(0);
            f.setComment("Research-assigned name; original symbol/source unavailable. Mode: "+(s.thumb?"Thumb":"ARM"));
            if(s.args.length>0) {
                ArrayList<Parameter> parameters=new ArrayList<>();
                for(String name:s.args)parameters.add(new ParameterImpl(name,UnsignedIntegerDataType.dataType,currentProgram));
                f.replaceParameters(Function.FunctionUpdateType.DYNAMIC_STORAGE_ALL_PARAMS,true,SourceType.USER_DEFINED,parameters.toArray(new Parameter[0]));
                f.setReturnType(UnsignedIntegerDataType.dataType,SourceType.USER_DEFINED);
            }
            functions.add(f);
        }
        // Direct callees need a known stack effect even before their bodies
        // are analyzed; otherwise restored LR is misidentified as a jump table.
        ArrayList<Address> targets=new ArrayList<>();
        for(Function f:functions) {
            InstructionIterator instructions=currentProgram.getListing().getInstructions(f.getBody(),true);
            while(instructions.hasNext()) {
                Instruction instruction=instructions.next();
                if(instruction.getFlowType().isCall())for(Address target:instruction.getFlows())targets.add(target);
            }
        }
        for(Address target:targets) {
            Function callee=currentProgram.getFunctionManager().getFunctionAt(target);
            if(callee==null)callee=currentProgram.getFunctionManager().createFunction("b220_unanalyzed_"+target,target,new AddressSet(target),SourceType.ANALYSIS);
            callee.setCallingConvention("__stdcall");callee.setStackPurgeSize(0);
        }
        for(Function f:functions) {
            InstructionIterator instructions=currentProgram.getListing().getInstructions(f.getBody(),true);
            while(instructions.hasNext())for(ghidra.program.model.symbol.Reference reference:instructions.next().getReferencesFrom()) {
                Address target=reference.getToAddress();
                if(reference.getReferenceType().isRead() && rom.contains(target) &&
                   currentProgram.getListing().getInstructionContaining(target)==null &&
                   currentProgram.getListing().getDefinedDataContaining(target)==null)
                    createData(target,UnsignedIntegerDataType.dataType);
            }
        }
        DecompInterface decompiler=new DecompInterface();
        if(!decompiler.openProgram(currentProgram))throw new IOException("Cannot open decompiler");
        StringBuilder index=new StringBuilder("address\tmode\tname\tdecompiled\n");int successful=0;
        try {
            for(int i=0;i<functions.size();i++) {
                Function f=functions.get(i);Seed s=seeds[i];println("Decompiling "+s.name);
                DecompileResults result=decompiler.decompileFunction(f,30,monitor);boolean ok=result.decompileCompleted();
                String text="/* Ghidra pseudocode; research-assigned name; not original or buildable source. */\n";
                text+=ok?result.getDecompiledFunction().getC():"/* ERROR: "+result.getErrorMessage()+" */\n";
                Files.writeString(output.resolve(s.name+".c"),text,StandardCharsets.UTF_8);
                StringBuilder asm=new StringBuilder();
                InstructionIterator instructions=currentProgram.getListing().getInstructions(f.getBody(),true);
                while(instructions.hasNext()) {Instruction instruction=instructions.next();asm.append(instruction.getAddress()).append("  ").append(instruction).append('\n');}
                Files.writeString(output.resolve(s.name+".asm"),asm,StandardCharsets.UTF_8);
                index.append(String.format("0x%x\t%s\t%s\t%s\n",s.start,s.thumb?"Thumb":"ARM",s.name,ok));
                if(ok)successful++;
            }
        } finally {decompiler.dispose();}
        Files.writeString(output.resolve("index.tsv"),index.toString(),StandardCharsets.UTF_8);
        Files.writeString(output.resolve("status.txt"),"functions="+functions.size()+"\ndecompiled="+successful+"\n",StandardCharsets.UTF_8);
        if(successful!=functions.size())throw new IOException("Some functions could not be decompiled");
        println("B220 pilot complete: "+successful+" functions");
    }
}
