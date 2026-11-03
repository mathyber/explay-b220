import java.util.Timer;
import java.util.TimerTask;

/* Threads, monitors and reflection; output is deterministic on any JVM. */
public class Threads {
    static class Slot {
        private int value;
        private boolean full;

        synchronized void put(int v) throws InterruptedException {
            while (full) wait();
            value = v;
            full = true;
            notifyAll();
        }

        synchronized int take() throws InterruptedException {
            while (!full) wait();
            full = false;
            notifyAll();
            return value;
        }
    }

    static class Counter {
        int count;
        synchronized void add() { count++; }
    }

    public static class Lazy {
        static { System.out.println("Lazy initialised"); }
        public Lazy() { System.out.println("Lazy constructed"); }
        public String toString() { return "a Lazy"; }
    }

    static int ticks;

    public static void main(String[] args) throws Exception {
        System.out.println(Thread.currentThread().getName());
        final Slot slot = new Slot();
        Thread producer = new Thread() {
            public void run() {
                try {
                    for (int i = 1; i <= 5; i++) slot.put(i * 10);
                } catch (InterruptedException e) {
                    System.out.println("producer interrupted");
                }
            }
        };
        producer.start();
        int total = 0;
        for (int i = 0; i < 5; i++) {
            int v = slot.take();
            total += v;
            System.out.println("took " + v);
        }
        producer.join();
        System.out.println("total " + total + " alive " + producer.isAlive());

        final int[] result = new int[1];
        Thread worker = new Thread(new Runnable() {
            public void run() { for (int i = 1; i <= 100; i++) result[0] += i; }
        });
        System.out.println(worker.getName());
        worker.start();
        worker.join();
        System.out.println("sum " + result[0]);

        Thread sleeper = new Thread() {
            public void run() {
                try {
                    Thread.sleep(100000);
                    System.out.println("woke normally");
                } catch (InterruptedException e) {
                    System.out.println("sleeper interrupted");
                }
            }
        };
        sleeper.start();
        Thread.sleep(20);
        sleeper.interrupt();
        sleeper.join();

        final Counter counter = new Counter();
        Thread[] adders = new Thread[3];
        for (int t = 0; t < adders.length; t++) {
            adders[t] = new Thread() {
                public void run() {
                    for (int i = 0; i < 1000; i++) {
                        counter.add();
                        if (i % 100 == 0) Thread.yield();
                    }
                }
            };
            adders[t].start();
        }
        for (int t = 0; t < adders.length; t++) adders[t].join();
        System.out.println("counter " + counter.count);

        Object lock = new Object();
        try {
            lock.notify();
        } catch (IllegalMonitorStateException e) {
            System.out.println("notify without lock rejected");
        }
        synchronized (lock) {
            long start = System.currentTimeMillis();
            lock.wait(30);
            System.out.println("timed wait returned " + (System.currentTimeMillis() - start >= 20));
        }

        Class c = Class.forName("Threads$Lazy");
        System.out.println("loaded " + c.getName());
        Object o = c.newInstance();
        System.out.println(o + " " + (o instanceof Lazy));
        try {
            Class.forName("no.such.Missing");
        } catch (ClassNotFoundException e) {
            System.out.println("missing class: " + e.getMessage());
        }

        final Object done = new Object();
        Timer timer = new Timer();
        timer.schedule(new TimerTask() {
            public void run() {
                synchronized (done) {
                    ticks++;
                    if (ticks == 3) done.notifyAll();
                }
            }
        }, 5, 5);
        synchronized (done) {
            while (ticks < 3) done.wait();
        }
        timer.cancel();
        System.out.println("timer ticked " + (ticks >= 3));
    }
}
