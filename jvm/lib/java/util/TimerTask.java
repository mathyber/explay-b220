package java.util;

public abstract class TimerTask implements Runnable {
    long next;
    long period;
    boolean cancelled;
    boolean scheduled;

    protected TimerTask() {}

    public abstract void run();

    public boolean cancel() {
        boolean pending = scheduled && !cancelled && (period > 0 || next > 0);
        cancelled = true;
        return pending;
    }

    public long scheduledExecutionTime() {
        return next - period;
    }
}
