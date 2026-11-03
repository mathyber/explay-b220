/* Deep recursion (Java stacks grow on demand) and many distinct monitors
   (entries are freed on the last unlock), compared with the JDK. */
public class Stacks {
    static int depth(int n, long a, double b, Object c) {
        if (n == 0) return c.hashCode() == 0 ? 0 : 1;
        long x = a + n;
        double y = b * 1.5;
        return 1 + depth(n - 1, x, y, c);
    }

    static synchronized int locked(Object o, int v) {
        synchronized (o) { return v + 1; }
    }

    public static void main(String[] args) throws Exception {
        System.out.println("depth " + depth(240, 1L, 2.0, "x"));
        int sum = 0;
        for (int i = 0; i < 1000; i++) sum = locked(new Object(), sum);
        System.out.println("monitors " + sum);
        final Object lock = new Object();
        final int[] seen = new int[1];
        Thread t = new Thread() {
            public void run() {
                synchronized (lock) {
                    seen[0] = depth(150, 0L, 0.0, "y");
                    lock.notify();
                }
            }
        };
        synchronized (lock) {
            t.start();
            lock.wait();
        }
        t.join();
        System.out.println("thread depth " + seen[0]);
    }
}
