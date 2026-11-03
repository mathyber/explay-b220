import java.util.Calendar;
import java.util.Date;
import java.util.TimeZone;

/* Calendar and TimeZone on fixed instants, compared with the JDK. */
public class Dates {
    static void show(Calendar c) {
        System.out.println(c.get(Calendar.YEAR) + "-" + (c.get(Calendar.MONTH) + 1) + "-" + c.get(Calendar.DAY_OF_MONTH)
            + " dow " + c.get(Calendar.DAY_OF_WEEK) + " " + c.get(Calendar.HOUR_OF_DAY) + ":" + c.get(Calendar.MINUTE)
            + ":" + c.get(Calendar.SECOND) + "." + c.get(Calendar.MILLISECOND) + " h12 " + c.get(Calendar.HOUR)
            + " ampm " + c.get(Calendar.AM_PM));
    }

    public static void main(String[] args) {
        Calendar c = Calendar.getInstance(TimeZone.getTimeZone("GMT"));
        long[] instants = {0L, 951782400000L, 1709251199999L, -1L, -86400000L * 365 * 3, 4102444800123L, 1793722743000L};
        for (int i = 0; i < instants.length; i++) {
            c.setTime(new Date(instants[i]));
            show(c);
        }
        c.setTime(new Date(1709251199999L));
        c.set(Calendar.DAY_OF_MONTH, 31);  // February 31st rolls into March
        show(c);
        c.set(Calendar.MONTH, 13);         // next year's February
        show(c);
        c.set(Calendar.HOUR_OF_DAY, 7);
        c.set(Calendar.MINUTE, 5);
        System.out.println(c.getTime().getTime());
        TimeZone moscow = TimeZone.getTimeZone("GMT+03:00");
        System.out.println(moscow.getID() + " " + moscow.getRawOffset() + " " + moscow.useDaylightTime());
        Calendar local = Calendar.getInstance(moscow);
        local.setTime(new Date(0));
        show(local);
    }
}
