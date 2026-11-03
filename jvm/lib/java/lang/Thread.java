package java.lang;

/* Green thread; start/currentThread/join come with the threading milestone. */
public class Thread implements Runnable {
    public static final int MIN_PRIORITY = 1;
    public static final int NORM_PRIORITY = 5;
    public static final int MAX_PRIORITY = 10;

    private Runnable target;
    private String name;
    private int priority = NORM_PRIORITY;

    public Thread() {}

    public Thread(Runnable target) {
        this.target = target;
    }

    public Thread(Runnable target, String name) {
        this.target = target;
        this.name = name;
    }

    public Thread(String name) {
        this.name = name;
    }

    public void run() {
        if (target != null) target.run();
    }

    public static native void sleep(long ms) throws InterruptedException;
    public static native void yield();

    public final String getName() { return name == null ? "Thread" : name; }
    public final int getPriority() { return priority; }
    public final void setPriority(int p) { priority = p; }
    public String toString() { return "Thread[" + getName() + "," + priority + "]"; }
}
