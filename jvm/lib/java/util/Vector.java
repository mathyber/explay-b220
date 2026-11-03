package java.util;

public class Vector {
    protected Object[] elementData;
    protected int elementCount;
    protected int capacityIncrement;

    public Vector() {
        this(10);
    }

    public Vector(int capacity) {
        this(capacity, 0);
    }

    public Vector(int capacity, int increment) {
        if (capacity < 0) throw new IllegalArgumentException();
        elementData = new Object[capacity];
        capacityIncrement = increment;
    }

    public synchronized void copyInto(Object[] array) {
        System.arraycopy(elementData, 0, array, 0, elementCount);
    }

    public synchronized void trimToSize() {
        if (elementCount < elementData.length) {
            Object[] smaller = new Object[elementCount];
            System.arraycopy(elementData, 0, smaller, 0, elementCount);
            elementData = smaller;
        }
    }

    public synchronized void ensureCapacity(int minimum) {
        if (minimum > elementData.length) {
            int size = capacityIncrement > 0 ? elementData.length + capacityIncrement : elementData.length * 2;
            if (size < minimum) size = minimum;
            Object[] bigger = new Object[size];
            System.arraycopy(elementData, 0, bigger, 0, elementCount);
            elementData = bigger;
        }
    }

    public synchronized void setSize(int size) {
        ensureCapacity(size);
        for (int i = size; i < elementCount; i++) elementData[i] = null;
        elementCount = size;
    }

    public int capacity() { return elementData.length; }
    public int size() { return elementCount; }
    public boolean isEmpty() { return elementCount == 0; }

    public synchronized Enumeration elements() {
        return new Enumeration() {
            int index;
            public boolean hasMoreElements() { return index < elementCount; }
            public Object nextElement() {
                if (index >= elementCount) throw new NoSuchElementException();
                return elementData[index++];
            }
        };
    }

    public boolean contains(Object o) { return indexOf(o, 0) >= 0; }
    public int indexOf(Object o) { return indexOf(o, 0); }

    public synchronized int indexOf(Object o, int from) {
        for (int i = from; i < elementCount; i++)
            if (o == null ? elementData[i] == null : o.equals(elementData[i])) return i;
        return -1;
    }

    public int lastIndexOf(Object o) { return lastIndexOf(o, elementCount - 1); }

    public synchronized int lastIndexOf(Object o, int from) {
        if (from >= elementCount) throw new IndexOutOfBoundsException();
        for (int i = from; i >= 0; i--)
            if (o == null ? elementData[i] == null : o.equals(elementData[i])) return i;
        return -1;
    }

    public synchronized Object elementAt(int index) {
        if (index < 0 || index >= elementCount) throw new ArrayIndexOutOfBoundsException(index);
        return elementData[index];
    }

    public synchronized Object firstElement() {
        if (elementCount == 0) throw new NoSuchElementException();
        return elementData[0];
    }

    public synchronized Object lastElement() {
        if (elementCount == 0) throw new NoSuchElementException();
        return elementData[elementCount - 1];
    }

    public synchronized void setElementAt(Object o, int index) {
        if (index < 0 || index >= elementCount) throw new ArrayIndexOutOfBoundsException(index);
        elementData[index] = o;
    }

    public synchronized void removeElementAt(int index) {
        if (index < 0 || index >= elementCount) throw new ArrayIndexOutOfBoundsException(index);
        System.arraycopy(elementData, index + 1, elementData, index, elementCount - index - 1);
        elementData[--elementCount] = null;
    }

    public synchronized void insertElementAt(Object o, int index) {
        if (index < 0 || index > elementCount) throw new ArrayIndexOutOfBoundsException(index);
        ensureCapacity(elementCount + 1);
        System.arraycopy(elementData, index, elementData, index + 1, elementCount - index);
        elementData[index] = o;
        elementCount++;
    }

    public synchronized void addElement(Object o) {
        ensureCapacity(elementCount + 1);
        elementData[elementCount++] = o;
    }

    public synchronized boolean removeElement(Object o) {
        int i = indexOf(o);
        if (i < 0) return false;
        removeElementAt(i);
        return true;
    }

    public synchronized void removeAllElements() {
        for (int i = 0; i < elementCount; i++) elementData[i] = null;
        elementCount = 0;
    }

    public synchronized String toString() {
        StringBuffer b = new StringBuffer("[");
        for (int i = 0; i < elementCount; i++) {
            if (i > 0) b.append(", ");
            b.append(String.valueOf(elementData[i]));
        }
        return b.append(']').toString();
    }
}
