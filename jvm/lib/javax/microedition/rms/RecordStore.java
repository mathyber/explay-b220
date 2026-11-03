package javax.microedition.rms;

import java.util.Hashtable;
import java.util.Vector;

/* Record stores kept in memory for the life of the VM (not saved to the card yet). */
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

    public static synchronized RecordStore openRecordStore(String name, boolean create) throws RecordStoreException {
        if (name == null || name.length() == 0 || name.length() > 32) throw new IllegalArgumentException();
        RecordStore store = (RecordStore) stores.get(name);
        if (store == null) {
            if (!create) throw new RecordStoreNotFoundException(name);
            store = new RecordStore(name);
            stores.put(name, store);
        }
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
        RecordStore store = (RecordStore) stores.get(name);
        if (store == null) throw new RecordStoreNotFoundException(name);
        if (store.opened > 0) throw new RecordStoreException("Record store is open");
        stores.remove(name);
    }

    public static synchronized String[] listRecordStores() {
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
