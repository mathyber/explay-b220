import java.util.Vector;

/* First Java program on the B220: console output, arithmetic, a thread. */
public class Hello {
    static int fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

    public static void main(String[] args) throws Exception {
        System.out.println("Hello from Java!");
        System.out.println("B220 + B22 JVM");
        System.out.println("fib(15) = " + fib(15));
        Vector v = new Vector();
        for (int i = 0; i < 5; i++) v.addElement("n" + i * i);
        System.out.println(v);
        System.out.println("pi ~ " + (Math.PI * 1000 / 1000));
        final int[] steps = new int[1];
        Thread worker = new Thread() {
            public void run() {
                for (int i = 1; i <= 3; i++) steps[0] += i;
            }
        };
        worker.start();
        worker.join();
        System.out.println("thread sum " + steps[0]);
        long start = System.currentTimeMillis();
        Thread.sleep(200);
        System.out.println("slept " + (System.currentTimeMillis() - start >= 200));
        System.out.println("free " + Runtime.getRuntime().freeMemory() / 1024 + " KB");
    }
}
