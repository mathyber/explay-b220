import java.util.Calendar;

/* Prints the wall clock as currentTimeMillis and Calendar see it. */
public class ClockTest {
    public static void main(String[] args) {
        long now = System.currentTimeMillis();
        Calendar c = Calendar.getInstance();
        System.out.println(now / 1000);
        System.out.println(c.get(Calendar.YEAR) + " " + c.get(Calendar.HOUR_OF_DAY) + ":" + c.get(Calendar.MINUTE));
    }
}
