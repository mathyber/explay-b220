package javax.microedition.rms;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.Hashtable;
import java.util.Vector;
import go1.Files;

/* Record stores saved on the memory card: E:\JAVA\RMS\<MIDlet-Name>\<store>.RMS,
   rewritten after every change, plus STORES.IDX with the store names. Without
   file access (a host with no card) they stay in memory for the life of the VM. */
public class RecordStore {
    public static final int AUTHMODE_PRIVATE = 0;
    public static final int AUTHMODE_ANY = 1;

    private static final Hashtable stores = new Hashtable();

    private final String name;
    final Vector ids = new Vector();
    final Vector records = new Vector();
    private final Vector listeners = new Vector();
    private int nextId = 1, version, opened;
    private long modified;

    private RecordStore(String name) { this.name = name; }

    /* ---------- card storage ---------- */

    private static String folder;
    private static boolean indexLoaded;

    private static String folder() {
        if (folder == null) {
            String suite = go1.Launcher.getProperty("MIDlet-Name");
            folder = "E:\\JAVA\\RMS\\" + Files.safeName(suite == null ? "APP" : suite);
        }
        return folder;
    }

    private static String fileOf(String name) {
        return folder() + "\\" + Files.safeName(name) + "_" + Integer.toHexString(name.hashCode()) + ".RMS";
    }

    /* Names of the stores saved on the card (into the in-memory table). */
    private static void loadIndex() {
        if (indexLoaded) return;
        indexLoaded = true;
        try {
            byte[] data = Files.readAll(folder() + "\\STORES.IDX");
            if (data == null) return;
            DataInputStream in = new DataInputStream(new java.io.ByteArrayInputStream(data));
            for (int n = in.readInt(); n > 0; n--) {
                String name = in.readUTF();
                if (!stores.containsKey(name)) stores.put(name, Boolean.TRUE);  /* on the card, not loaded */
            }
        } catch (Throwable e) {}
    }

    private static void saveIndex() {
        try {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            DataOutputStream out = new DataOutputStream(bytes);
            out.writeInt(stores.size());
            for (java.util.Enumeration e = stores.keys(); e.hasMoreElements();) out.writeUTF((String) e.nextElement());
            Files.makeDirs(folder());
            Files.writeAll(folder() + "\\STORES.IDX", bytes.toByteArray(), bytes.size());
        } catch (Throwable e) {}
    }

    private static RecordStore load(String name) {
        try {
            byte[] data = Files.readAll(fileOf(name));
            if (data == null) return null;
            DataInputStream in = new DataInputStream(new java.io.ByteArrayInputStream(data));
            if (in.readInt() != 0x474f3152 || !in.readUTF().equals(name)) return null;  /* "GO1R" */
            RecordStore store = new RecordStore(name);
            store.version = in.readInt();
            store.nextId = in.readInt();
            store.modified = in.readLong();
            for (int n = in.readInt(); n > 0; n--) {
                store.ids.addElement(new Integer(in.readInt()));
                byte[] record = new byte[in.readInt()];
                in.readFully(record);
                store.records.addElement(record);
            }
            return store;
        } catch (Throwable e) {
            return null;
        }
    }

    private void save() {
        try {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            DataOutputStream out = new DataOutputStream(bytes);
            out.writeInt(0x474f3152);
            out.writeUTF(name);
            out.writeInt(version);
            out.writeInt(nextId);
            out.writeLong(modified);
            out.writeInt(ids.size());
            for (int i = 0; i < ids.size(); i++) {
                byte[] record = (byte[]) records.elementAt(i);
                out.writeInt(((Integer) ids.elementAt(i)).intValue());
                out.writeInt(record.length);
                out.write(record);
            }
            Files.makeDirs(folder());
            Files.writeAll(fileOf(name), bytes.toByteArray(), bytes.size());
        } catch (Throwable e) {}
    }

    public static synchronized RecordStore openRecordStore(String name, boolean create) throws RecordStoreException {
        if (name == null || name.length() == 0 || name.length() > 32) throw new IllegalArgumentException();
        loadIndex();
        Object known = stores.get(name);
        RecordStore store = known instanceof RecordStore ? (RecordStore) known : load(name);
        if (store == null) {
            if (!create) throw new RecordStoreNotFoundException(name);
            store = new RecordStore(name);
            stores.put(name, store);
            store.save();
            saveIndex();
        } else stores.put(name, store);
        store.opened++;
        return store;
    }

    public static RecordStore openRecordStore(String name, boolean create, int authmode, boolean writable) throws RecordStoreException {
        return openRecordStore(name, create);
    }

    public static RecordStore openRecordStore(String name, String vendor, String suite) throws RecordStoreException {
        return openRecordStore(name, false);
    }

    public static synchronized void deleteRecordStore(String name) throws RecordStoreException {
        loadIndex();
        Object store = stores.get(name);
        if (store == null) throw new RecordStoreNotFoundException(name);
        if (store instanceof RecordStore && ((RecordStore) store).opened > 0) throw new RecordStoreException("Record store is open");
        stores.remove(name);
        try { Files.remove(fileOf(name)); } catch (Throwable e) {}
        saveIndex();
    }

    public static synchronized String[] listRecordStores() {
        loadIndex();
        if (stores.isEmpty()) return null;
        String[] names = new String[stores.size()];
        int i = 0;
        for (java.util.Enumeration e = stores.keys(); e.hasMoreElements();) names[i++] = (String) e.nextElement();
        return names;
    }

    private void check() throws RecordStoreNotOpenException {
        if (opened <= 0) throw new RecordStoreNotOpenException(name);
    }

    private int index(int id) throws RecordStoreException {
        check();
        for (int i = 0; i < ids.size(); i++) if (((Integer) ids.elementAt(i)).intValue() == id) return i;
        throw new InvalidRecordIDException(String.valueOf(id));
    }

    private void changed() {
        version++;
        modified = System.currentTimeMillis();
        save();
    }

    private static byte[] copy(byte[] data, int offset, int length) {
        byte[] out = new byte[length];
        if (length > 0) System.arraycopy(data, offset, out, 0, length);
        return out;
    }

    public synchronized void closeRecordStore() throws RecordStoreException {
        check();
        if (--opened == 0) listeners.removeAllElements();
    }

    public String getName() throws RecordStoreNotOpenException { check(); return name; }
    public int getVersion() throws RecordStoreNotOpenException { check(); return version; }
    public long getLastModified() throws RecordStoreNotOpenException { check(); return modified; }
    public int getNumRecords() throws RecordStoreNotOpenException { check(); return ids.size(); }
    public int getNextRecordID() throws RecordStoreException { check(); return nextId; }
    public int getSizeAvailable() throws RecordStoreNotOpenException { check(); return 64 * 1024; }

    public int getSize() throws RecordStoreNotOpenException {
        check();
        int size = 0;
        for (int i = 0; i < records.size(); i++) size += ((byte[]) records.elementAt(i)).length;
        return size;
    }

    public synchronized int addRecord(byte[] data, int offset, int length) throws RecordStoreException {
        check();
        int id = nextId++;
        ids.addElement(new Integer(id));
        records.addElement(copy(data, offset, length));
        changed();
        for (int i = 0; i < listeners.size(); i++) ((RecordListener) listeners.elementAt(i)).recordAdded(this, id);
        return id;
    }

    public synchronized void deleteRecord(int id) throws RecordStoreException {
        int i = index(id);
        ids.removeElementAt(i);
        records.removeElementAt(i);
        changed();
        for (int j = 0; j < listeners.size(); j++) ((RecordListener) listeners.elementAt(j)).recordDeleted(this, id);
    }

    public synchronized void setRecord(int id, byte[] data, int offset, int length) throws RecordStoreException {
        records.setElementAt(copy(data, offset, length), index(id));
        changed();
        for (int i = 0; i < listeners.size(); i++) ((RecordListener) listeners.elementAt(i)).recordChanged(this, id);
    }

    public synchronized byte[] getRecord(int id) throws RecordStoreException {
        byte[] data = (byte[]) records.elementAt(index(id));
        return data.length == 0 ? null : copy(data, 0, data.length);
    }

    public synchronized int getRecord(int id, byte[] buffer, int offset) throws RecordStoreException {
        byte[] data = (byte[]) records.elementAt(index(id));
        System.arraycopy(data, 0, buffer, offset, data.length);
        return data.length;
    }

    public synchronized int getRecordSize(int id) throws RecordStoreException {
        return ((byte[]) records.elementAt(index(id))).length;
    }

    public void addRecordListener(RecordListener l) { if (!listeners.contains(l)) listeners.addElement(l); }
    public void removeRecordListener(RecordListener l) { listeners.removeElement(l); }
    public void setMode(int authmode, boolean writable) throws RecordStoreException { check(); }

    public RecordEnumeration enumerateRecords(RecordFilter filter, RecordComparator comparator, boolean keepUpdated) throws RecordStoreNotOpenException {
        check();
        return new Records(this, filter, comparator);
    }

    /* Snapshot of matching record ids, sorted by the comparator. */
    private static class Records implements RecordEnumeration {
        private final RecordStore store;
        private final RecordFilter filter;
        private final RecordComparator comparator;
        private int[] found;
        private int at;

        Records(RecordStore store, RecordFilter filter, RecordComparator comparator) {
            this.store = store;
            this.filter = filter;
            this.comparator = comparator;
            rebuild();
        }

        public void rebuild() {
            Vector ids = new Vector(), data = new Vector();
            synchronized (store) {
                for (int i = 0; i < store.ids.size(); i++) {
                    byte[] record = (byte[]) store.records.elementAt(i);
                    if (filter != null && !filter.matches(record)) continue;
                    int j = ids.size();
                    while (comparator != null && j > 0 && comparator.compare((byte[]) data.elementAt(j - 1), record) == RecordComparator.FOLLOWS) j--;
                    ids.insertElementAt(store.ids.elementAt(i), j);
                    data.insertElementAt(record, j);
                }
            }
            found = new int[ids.size()];
            for (int i = 0; i < found.length; i++) found[i] = ((Integer) ids.elementAt(i)).intValue();
            at = 0;
        }

        public int numRecords() { return found.length; }
        public boolean hasNextElement() { return at < found.length; }
        public boolean hasPreviousElement() { return at > 0; }
        public void reset() { at = 0; }
        public void keepUpdated(boolean keep) {}
        public boolean isKeptUpdated() { return false; }
        public void destroy() {}

        public int nextRecordId() throws InvalidRecordIDException {
            if (at >= found.length) throw new InvalidRecordIDException();
            return found[at++];
        }

        public int previousRecordId() throws InvalidRecordIDException {
            if (at <= 0) throw new InvalidRecordIDException();
            return found[--at];
        }

        public byte[] nextRecord() throws RecordStoreException { return store.getRecord(nextRecordId()); }
        public byte[] previousRecord() throws RecordStoreException { return store.getRecord(previousRecordId()); }
    }
}
