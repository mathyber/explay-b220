package java.util;

/* Gregorian calendar (CLDC subset): fields are computed from the time when read. */
public abstract class Calendar {
    public static final int YEAR = 1, MONTH = 2, DATE = 5, DAY_OF_MONTH = 5, DAY_OF_WEEK = 7, AM_PM = 9,
        HOUR = 10, HOUR_OF_DAY = 11, MINUTE = 12, SECOND = 13, MILLISECOND = 14;
    public static final int JANUARY = 0, FEBRUARY = 1, MARCH = 2, APRIL = 3, MAY = 4, JUNE = 5, JULY = 6,
        AUGUST = 7, SEPTEMBER = 8, OCTOBER = 9, NOVEMBER = 10, DECEMBER = 11;
    public static final int SUNDAY = 1, MONDAY = 2, TUESDAY = 3, WEDNESDAY = 4, THURSDAY = 5, FRIDAY = 6, SATURDAY = 7;
    public static final int AM = 0, PM = 1;
    private static final long DAY = 86400000L;

    protected int[] fields = new int[15];
    protected boolean[] isSet = new boolean[15];
    protected long time;
    private TimeZone zone;

    protected Calendar() {
        zone = TimeZone.getDefault();
        time = System.currentTimeMillis();
    }

    public static synchronized Calendar getInstance() { return new Gregorian(); }

    public static synchronized Calendar getInstance(TimeZone zone) {
        Calendar c = new Gregorian();
        c.setTimeZone(zone);
        return c;
    }

    public final Date getTime() { return new Date(time); }
    public final void setTime(Date date) { time = date.getTime(); }
    protected long getTimeInMillis() { return time; }
    protected void setTimeInMillis(long millis) { time = millis; }
    public TimeZone getTimeZone() { return zone; }
    public void setTimeZone(TimeZone value) { zone = value; }

    protected void computeFields() {
        long local = time + zone.getRawOffset();
        long days = local / DAY;
        long ms = local % DAY;
        if (ms < 0) { ms += DAY; days--; }
        // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
        long z = days + 719468;
        long era = (z >= 0 ? z : z - 146096) / 146097;
        long doe = z - era * 146097;
        long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        long mp = (5 * doy + 2) / 153;
        int day = (int) (doy - (153 * mp + 2) / 5 + 1);
        int month = (int) (mp < 10 ? mp + 3 : mp - 9);
        int year = (int) (yoe + era * 400 + (month <= 2 ? 1 : 0));
        fields[YEAR] = year;
        fields[MONTH] = month - 1;
        fields[DAY_OF_MONTH] = day;
        fields[DAY_OF_WEEK] = (int) (((days + 4) % 7 + 7) % 7) + 1;
        int hour = (int) (ms / 3600000);
        fields[HOUR_OF_DAY] = hour;
        fields[AM_PM] = hour < 12 ? AM : PM;
        fields[HOUR] = hour % 12;
        fields[MINUTE] = (int) (ms / 60000 % 60);
        fields[SECOND] = (int) (ms / 1000 % 60);
        fields[MILLISECOND] = (int) (ms % 1000);
    }

    protected void computeTime() {
        int year = fields[YEAR], month = fields[MONTH];
        year += month >= 0 ? month / 12 : (month - 11) / 12;
        month = ((month % 12) + 12) % 12 + 1;
        long y = month <= 2 ? year - 1 : year;
        long era = (y >= 0 ? y : y - 399) / 400;
        long yoe = y - era * 400;
        long doy = (153 * (month > 2 ? month - 3 : month + 9) + 2) / 5;
        long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        long days = era * 146097 + doe - 719468 + fields[DAY_OF_MONTH] - 1;
        long ms = ((fields[HOUR_OF_DAY] * 60L + fields[MINUTE]) * 60 + fields[SECOND]) * 1000 + fields[MILLISECOND];
        time = days * DAY + ms - zone.getRawOffset();
    }

    public final int get(int field) {
        computeFields();
        return fields[field];
    }

    public final void set(int field, int value) {
        computeFields();
        if (field == HOUR) fields[HOUR_OF_DAY] = fields[AM_PM] * 12 + value;
        else if (field == AM_PM) fields[HOUR_OF_DAY] = value * 12 + fields[HOUR];
        else fields[field] = value;
        isSet[field] = true;
        computeTime();
    }

    public boolean equals(Object o) { return o instanceof Calendar && ((Calendar) o).time == time; }
    public boolean before(Object o) { return o instanceof Calendar && time < ((Calendar) o).time; }
    public boolean after(Object o) { return o instanceof Calendar && time > ((Calendar) o).time; }

    private static class Gregorian extends Calendar {}
}
