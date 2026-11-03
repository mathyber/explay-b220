package java.lang;

public class Object {
    public Object() {}

    public final native Class getClass();

    public native int hashCode();

    /* Arrays only (as on CLDC); other objects throw CloneNotSupportedException. */
    protected native Object clone();

    public boolean equals(Object other) {
        return this == other;
    }

    public String toString() {
        return getClass().getName() + "@" + Integer.toHexString(hashCode());
    }

    public final native void notify();

    public final native void notifyAll();

    public final void wait() throws InterruptedException {
        wait(0);
    }

    public final native void wait(long timeout) throws InterruptedException;

    public final void wait(long timeout, int nanos) throws InterruptedException {
        wait(timeout + (nanos > 0 ? 1 : 0));
    }
}
