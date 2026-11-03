package java.util;

public class Hashtable {
    private static class Entry {
        final int hash;
        final Object key;
        Object value;
        Entry next;

        Entry(int hash, Object key, Object value, Entry next) {
            this.hash = hash;
            this.key = key;
            this.value = value;
            this.next = next;
        }
    }

    private Entry[] table;
    private int count;
    private int threshold;

    public Hashtable() {
        this(11);
    }

    public Hashtable(int capacity) {
        if (capacity < 0) throw new IllegalArgumentException();
        if (capacity == 0) capacity = 1;
        table = new Entry[capacity];
        threshold = capacity * 3 / 4;
    }

    public int size() { return count; }
    public boolean isEmpty() { return count == 0; }

    private static int index(int hash, int length) {
        return (hash & 0x7fffffff) % length;
    }

    public synchronized boolean contains(Object value) {
        if (value == null) throw new NullPointerException();
        for (int i = 0; i < table.length; i++)
            for (Entry e = table[i]; e != null; e = e.next) if (e.value.equals(value)) return true;
        return false;
    }

    public synchronized boolean containsKey(Object key) {
        int hash = key.hashCode();
        for (Entry e = table[index(hash, table.length)]; e != null; e = e.next)
            if (e.hash == hash && e.key.equals(key)) return true;
        return false;
    }

    public synchronized Object get(Object key) {
        int hash = key.hashCode();
        for (Entry e = table[index(hash, table.length)]; e != null; e = e.next)
            if (e.hash == hash && e.key.equals(key)) return e.value;
        return null;
    }

    protected void rehash() {
        Entry[] old = table;
        Entry[] bigger = new Entry[old.length * 2 + 1];
        for (int i = 0; i < old.length; i++) {
            for (Entry e = old[i]; e != null; ) {
                Entry next = e.next;
                int at = index(e.hash, bigger.length);
                e.next = bigger[at];
                bigger[at] = e;
                e = next;
            }
        }
        table = bigger;
        threshold = bigger.length * 3 / 4;
    }

    public synchronized Object put(Object key, Object value) {
        if (value == null) throw new NullPointerException();
        int hash = key.hashCode();
        int at = index(hash, table.length);
        for (Entry e = table[at]; e != null; e = e.next) {
            if (e.hash == hash && e.key.equals(key)) {
                Object old = e.value;
                e.value = value;
                return old;
            }
        }
        if (count >= threshold) {
            rehash();
            at = index(hash, table.length);
        }
        table[at] = new Entry(hash, key, value, table[at]);
        count++;
        return null;
    }

    public synchronized Object remove(Object key) {
        int hash = key.hashCode();
        int at = index(hash, table.length);
        for (Entry e = table[at], previous = null; e != null; previous = e, e = e.next) {
            if (e.hash == hash && e.key.equals(key)) {
                if (previous == null) table[at] = e.next; else previous.next = e.next;
                count--;
                return e.value;
            }
        }
        return null;
    }

    public synchronized void clear() {
        for (int i = 0; i < table.length; i++) table[i] = null;
        count = 0;
    }

    private Enumeration enumerate(final boolean keys) {
        return new Enumeration() {
            int bucket = table.length;
            Entry entry;

            public boolean hasMoreElements() {
                while (entry == null && bucket > 0) entry = table[--bucket];
                return entry != null;
            }

            public Object nextElement() {
                if (!hasMoreElements()) throw new NoSuchElementException();
                Entry e = entry;
                entry = e.next;
                return keys ? e.key : e.value;
            }
        };
    }

    public synchronized Enumeration keys() { return enumerate(true); }
    public synchronized Enumeration elements() { return enumerate(false); }

    public synchronized String toString() {
        StringBuffer b = new StringBuffer("{");
        boolean first = true;
        for (int i = table.length - 1; i >= 0; i--) {
            for (Entry e = table[i]; e != null; e = e.next) {
                if (!first) b.append(", ");
                first = false;
                b.append(String.valueOf(e.key)).append('=').append(String.valueOf(e.value));
            }
        }
        return b.append('}').toString();
    }
}
