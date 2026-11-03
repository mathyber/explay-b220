package java.lang;

/* Green thread scheduled by the VM. */
public class Thread implements Runnable {
    public static final int MIN_PRIORITY = 1;
    public static final int NORM_PRIORITY = 5;
    public static final int MAX_PRIORITY = 10;
    private static int created;

    /* Native thread pointer, set by the VM. */
    int vmThread;
    private Runnable target;
    private String name;
    private int priority = NORM_PRIORITY;

    public Thread() {
        this(null, null);
    }

    public Thread(Runnable target) {
        this(target, null);
    }

    public Thread(String name) {
        this(null, name);
    }

    public Thread(Runnable target, String name) {
        this.target = target;
        this.name = name != null ? name : "Thread-" + created++;
    }

    public void run() {
        if (target != null) target.run();
    }

    public native void start();
    public final native boolean isAlive();
    public native void interrupt();
    public native boolean isInterrupted();
    public static native boolean interrupted();
    public static native Thread currentThread();
    public static native int activeCount();
    public static native void sleep(long ms) throws InterruptedException;
    public static native void yield();

    public final void join() throws InterruptedException {
        while (isAlive()) sleep(10);
    }

    public final String getName() { return name == null ? "main" : name; }
    public final int getPriority() { return priority; }

    public final void setPriority(int p) {
        if (p < MIN_PRIORITY || p > MAX_PRIORITY) throw new IllegalArgumentException();
        priority = p;
    }

    public String toString() { return "Thread[" + getName() + "," + priority + "]"; }
}
