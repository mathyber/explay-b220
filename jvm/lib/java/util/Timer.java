package java.util;

/* One background thread runs the tasks in time order. */
public class Timer {
    private final Vector tasks = new Vector();
    private boolean cancelled;

    public Timer() {
        Thread worker = new Thread() {
            public void run() { loop(); }
        };
        worker.start();
    }

    private void loop() {
        while (true) {
            TimerTask task;
            synchronized (tasks) {
                while (!cancelled && tasks.isEmpty()) {
                    try { tasks.wait(); } catch (InterruptedException e) { return; }
                }
                if (cancelled) return;
                task = (TimerTask) tasks.elementAt(0);
                for (int i = 1; i < tasks.size(); i++) {
                    TimerTask t = (TimerTask) tasks.elementAt(i);
                    if (t.next < task.next) task = t;
                }
                if (task.cancelled) {
                    tasks.removeElement(task);
                    continue;
                }
                long wait = task.next - System.currentTimeMillis();
                if (wait > 0) {
                    try { tasks.wait(wait); } catch (InterruptedException e) { return; }
                    continue;
                }
                if (task.period > 0) task.next += task.period;
                else {
                    tasks.removeElement(task);
                    task.next = 0;
                }
            }
            try {
                task.run();
            } catch (RuntimeException e) {
                synchronized (tasks) {
                    cancelled = true;
                    tasks.removeAllElements();
                }
                return;
            }
        }
    }

    private void add(TimerTask task, long first, long period) {
        if (first < 0 || period < 0) throw new IllegalArgumentException();
        synchronized (tasks) {
            if (cancelled || task.scheduled) throw new IllegalStateException();
            task.scheduled = true;
            task.next = System.currentTimeMillis() + first;
            task.period = period;
            tasks.addElement(task);
            tasks.notify();
        }
    }

    public void schedule(TimerTask task, long delay) { add(task, delay, 0); }
    public void schedule(TimerTask task, long delay, long period) {
        if (period <= 0) throw new IllegalArgumentException();
        add(task, delay, period);
    }
    public void scheduleAtFixedRate(TimerTask task, long delay, long period) { schedule(task, delay, period); }
    public void schedule(TimerTask task, Date time) { add(task, Math.max(0, time.getTime() - System.currentTimeMillis()), 0); }

    public void cancel() {
        synchronized (tasks) {
            cancelled = true;
            tasks.removeAllElements();
            tasks.notify();
        }
    }
}
