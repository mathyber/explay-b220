package java.util;

public class Date {
    private long time;

    public Date() {
        this(System.currentTimeMillis());
    }

    public Date(long time) {
        this.time = time;
    }

    public long getTime() { return time; }
    public void setTime(long time) { this.time = time; }
    public boolean equals(Object o) { return o instanceof Date && ((Date) o).time == time; }
    public int hashCode() { return (int) (time ^ (time >>> 32)); }
}
