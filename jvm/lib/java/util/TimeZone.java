package java.util;

/* Fixed-offset zones only ("GMT", "UTC", "GMT+03:00"); no daylight saving. */
public abstract class TimeZone {
    private static TimeZone defaultZone;

    public TimeZone() {}

    public abstract int getOffset(int era, int year, int month, int day, int dayOfWeek, int millis);
    public abstract int getRawOffset();
    public abstract boolean useDaylightTime();

    public String getID() { return "GMT"; }

    public static synchronized TimeZone getDefault() {
        if (defaultZone == null) defaultZone = new Fixed("GMT", 0);
        return defaultZone;
    }

    public static TimeZone getTimeZone(String id) {
        if (id == null) throw new NullPointerException();
        if (id.equals("GMT") || id.equals("UTC")) return new Fixed(id, 0);
        if (id.startsWith("GMT") && id.length() > 4) {
            try {
                int sign = id.charAt(3) == '-' ? -1 : 1;
                String rest = id.substring(4);
                int colon = rest.indexOf(':');
                int hours = Integer.parseInt(colon < 0 ? rest : rest.substring(0, colon));
                int minutes = colon < 0 ? 0 : Integer.parseInt(rest.substring(colon + 1));
                return new Fixed(id, sign * (hours * 60 + minutes) * 60000);
            } catch (RuntimeException e) {
                // Unknown ids fall back to GMT, as on CLDC.
            }
        }
        return new Fixed("GMT", 0);
    }

    public static String[] getAvailableIDs() { return new String[] {"GMT", "UTC"}; }

    private static class Fixed extends TimeZone {
        private final String id;
        private final int offset;

        Fixed(String id, int offset) {
            this.id = id;
            this.offset = offset;
        }

        public int getOffset(int era, int year, int month, int day, int dayOfWeek, int millis) { return offset; }
        public int getRawOffset() { return offset; }
        public boolean useDaylightTime() { return false; }
        public String getID() { return id; }
    }
}
